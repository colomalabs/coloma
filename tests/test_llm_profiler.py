import asyncio
import os
import threading
from types import SimpleNamespace

import pytest
from httpx import Request
from openai import APITimeoutError

from backend.llm_profiler import (
    DEFAULT_COMPLETION_TOKENS,
    MAX_SWEEP_OOM_MAX_MODEL_LEN_HALVING_RETRIES,
    MAX_NUM_SEQS_VALUES,
    MIN_MAX_MODEL_LEN,
    BenchmarkSkipped,
    BenchPoint,
    OomRecoveryOptions,
    SweepCudaOutOfMemory,
    container_root_cause,
    BenchMeasurement,
    LlmProfiler,
    ProfilerConfig,
    ServerInfo,
    StressTestResult,
    validate_config,
)
from backend.vllm_bench_client import RequestSample


def test_profiler_cache_defaults_use_standard_home_cache_paths():
    config = ProfilerConfig(model_name="test-model")

    assert config.hf_home == "~/.cache/huggingface"
    assert config.vllm_home == "~/.cache/vllm"
    validate_config(config)


def test_docker_mounts_expand_profiler_cache_home_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    config = ProfilerConfig(model_name="test-model")

    cmd = LlmProfiler.build_docker_cmd(config, "fake-container", max_num_seqs=1, max_model_len=None,
                                       ensure_cache_dirs=False)

    hf_home = os.path.abspath(os.path.expanduser("~/.cache/huggingface"))
    vllm_home = os.path.abspath(os.path.expanduser("~/.cache/vllm"))
    assert f"{hf_home}:/root/.cache/huggingface" in cmd
    assert f"{vllm_home}:/root/.cache/vllm" in cmd


def test_docker_command_includes_fp8_flags_by_default():
    config = ProfilerConfig(model_name="test-model")

    cmd = LlmProfiler.build_docker_cmd(config, "fake-container", max_num_seqs=1, max_model_len=None,
                                       ensure_cache_dirs=False)

    assert "--kv-cache-dtype" in cmd
    assert "--quantization" in cmd
    assert cmd[cmd.index("--kv-cache-dtype") + 1] == "fp8"
    assert cmd[cmd.index("--quantization") + 1] == "fp8"


def test_docker_command_omits_fp8_flags_when_disabled():
    config = ProfilerConfig(model_name="test-model", fp8=False)

    cmd = LlmProfiler.build_docker_cmd(config, "fake-container", max_num_seqs=1, max_model_len=None,
                                       ensure_cache_dirs=False)

    assert "--kv-cache-dtype" not in cmd
    assert "--quantization" not in cmd


class SkipObserver:
    """Only the pieces of StepObserver that the sweep touches."""

    def __init__(self):
        self.skip_event = asyncio.Event()
        self.points = []
        self.stress_tests = []
        self.progress: list[tuple[int, int]] = []
        self.timeout_num_seqs = None
        self.skippable = False
        self.context_lengths: list[tuple[int, str]] = []
        self.context_length_messages: list[str] = []

    def is_cancelled(self) -> bool:
        return False

    def is_benchmark_skipped(self) -> bool:
        return self.skip_event.is_set()

    async def wait_benchmark_skipped(self) -> None:
        await self.skip_event.wait()

    def clear_benchmark_skip(self) -> None:
        self.skip_event.clear()

    def add_bench_point(self, point) -> None:
        self.points.append(point)

    def add_stress_test(self, result) -> None:
        self.stress_tests.append(result)

    def set_benchmark_timeout(self, num_seqs: int) -> None:
        self.timeout_num_seqs = num_seqs

    def set_process(self, proc) -> None:
        pass

    def set_benchmark_skippable(self, skippable: bool) -> None:
        self.skippable = skippable

    def set_context_length_capped(
        self,
        capped_max_model_len: int,
        reason: str = "oom",
        max_num_seqs: int | None = None,
        message: str = "",
    ) -> None:
        self.context_lengths.append((capped_max_model_len, reason, max_num_seqs))
        self.context_length_messages.append(message)

    def set_benchmark_progress(self, done: int, total: int) -> None:
        self.progress.append((done, total))

    def update_step(self, *args, **kwargs) -> None:
        pass


def make_profiler(observer: SkipObserver, config: ProfilerConfig | None = None) -> LlmProfiler:
    profiler = LlmProfiler(config or ProfilerConfig(model_name="test-model"), "fake-container", observer)

    async def skip_full_context_stress(*args) -> None:
        pass

    profiler.run_full_context_stress = skip_full_context_stress
    return profiler


def test_model_download_drains_output_while_process_is_running(monkeypatch, tmp_path):
    drained = threading.Event()

    class LargeOutput:
        remaining = 1_000_000

        def read(self, size: int) -> str:
            if self.remaining == 0:
                drained.set()
                return ""
            count = min(size, self.remaining)
            self.remaining -= count
            return "x" * count

    class OutputBoundProcess:
        def __init__(self):
            self.stdout = LargeOutput()
            self.returncode = None

        def poll(self):
            if drained.wait(timeout=0.2):
                self.returncode = 0
            return self.returncode

    monkeypatch.setattr("backend.llm_profiler.subprocess.Popen", lambda *args, **kwargs: OutputBoundProcess())
    observer = SkipObserver()
    config = ProfilerConfig(model_name="test-model", hf_home=str(tmp_path / "hf"))
    profiler = LlmProfiler(config, "fake-container", observer)

    async def scenario():
        try:
            await profiler.download_model()
        finally:
            await profiler.bench.aclose()

    asyncio.run(scenario())

    assert drained.is_set()


def test_skipping_the_sweep_keeps_the_points_already_measured():
    observer = SkipObserver()
    profiler = make_profiler(observer)

    async def fake_batch(num_seqs, n_tokens):
        # Skip requested while the second point is in flight.
        if num_seqs == 2:
            observer.skip_event.set()
            await asyncio.sleep(60)
        return BenchMeasurement(
            median_prompt_tokens=n_tokens, completion_tokens=128, duration=2.0, median_ttft=0.2,
            average_itl=0.01, median_itl=0.008, system_throughput=120.0,
        )

    profiler.run_batch = fake_batch

    async def scenario():
        try:
            return await profiler.run_sweep(32, [1, 2, 4], [1024])
        finally:
            await profiler.bench.aclose()

    points, skipped = asyncio.run(scenario())

    assert skipped
    assert [point.concurrent_requests for point in points] == [1]
    assert observer.points == points
    # Every point carries the --max-num-seqs of the server that served it, which is what the charts
    # are grouped and labelled by.
    assert [point.max_num_seqs for point in points] == [32]


def test_sweep_point_returns_its_measurement_when_no_skip_is_requested():
    observer = SkipObserver()
    profiler = make_profiler(observer)

    measurement = BenchMeasurement(
        median_prompt_tokens=1024, completion_tokens=128, duration=2.0, median_ttft=0.2,
        average_itl=0.01, median_itl=0.008, system_throughput=120.0,
    )

    async def fake_batch(num_seqs, n_tokens):
        return measurement

    profiler.run_batch = fake_batch

    async def scenario():
        try:
            return await profiler.run_prompt_batch_or_skip(4, 1024)
        finally:
            await profiler.bench.aclose()

    assert asyncio.run(scenario()) == measurement


def test_run_batch_measures_the_batch_wall_clock_at_the_configured_completion_length():
    observer = SkipObserver()
    config = ProfilerConfig(model_name="test-model", completion_tokens=64)
    profiler = make_profiler(observer, config)
    batches: list[tuple[int, int]] = []

    class FakeBench:
        async def build_prompts(self, model_name, num_seqs, n_tokens):
            return ["prompt"] * num_seqs

        async def run_batch(self, model_name, prompts, completion_tokens):
            batches.append((len(prompts), completion_tokens))
            return [
                RequestSample(prompt_tokens=1024, completion_tokens=64, ttft=0.5 * index,
                              mean_itl=0.05 * index, median_itl=0.01 * index, decoding_throughput=20.0)
                for index in (1, 2, 3)
            ]

        async def aclose(self):
            pass

    async def scenario():
        real_bench = profiler.bench
        profiler.bench = FakeBench()
        try:
            return await profiler.run_batch(3, 1024)
        finally:
            await real_bench.aclose()

    measured = asyncio.run(scenario())

    # The sweep decodes the configured number of tokens, not a hardcoded one.
    assert batches == [(3, 64)]
    assert measured.completion_tokens == 64
    # The batch's wall clock is kept: it is the job duration the calculator validates against.
    assert measured.duration > 0.0
    assert measured.median_ttft == pytest.approx(1.0)
    assert measured.average_itl == pytest.approx(0.1)
    assert measured.median_itl == pytest.approx(0.02)
    assert measured.system_throughput > 0.0


def test_sweep_records_openai_api_timeout_and_keeps_prior_points():
    observer = SkipObserver()
    profiler = make_profiler(observer)

    async def fake_batch(num_seqs, n_tokens):
        if num_seqs == 2:
            raise APITimeoutError(request=Request("POST", "http://test"))
        return MEASUREMENT

    profiler.run_batch = fake_batch

    async def scenario():
        try:
            return await profiler.run_sweep(32, [1, 2, 4], [1024])
        finally:
            await profiler.bench.aclose()

    points, skipped = asyncio.run(scenario())

    assert not skipped
    assert [point.concurrent_requests for point in points] == [1]
    assert observer.timeout_num_seqs == 2


def test_sweep_point_abandons_the_in_flight_batch_when_the_user_skips():
    observer = SkipObserver()
    profiler = make_profiler(observer)
    cancelled = asyncio.Event()

    async def slow_batch(num_seqs, n_tokens):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        raise AssertionError("the batch should not have run to completion")

    profiler.run_batch = slow_batch

    async def scenario():
        try:
            point = asyncio.ensure_future(profiler.run_prompt_batch_or_skip(4, 1024))
            await asyncio.sleep(0)
            observer.skip_event.set()
            return await point
        finally:
            await profiler.bench.aclose()

    # No measurement, and the requests already in flight were dropped instead of awaited.
    assert asyncio.run(scenario()) is None
    assert cancelled.is_set()


MEASUREMENT = BenchMeasurement(
    median_prompt_tokens=1024, completion_tokens=128, duration=2.0, median_ttft=0.2,
    average_itl=0.01, median_itl=0.008, system_throughput=120.0,
)


class ProfileObserver(SkipObserver):
    """SkipObserver plus what profile() needs beyond the sweep itself."""

    def __init__(self):
        super().__init__()
        self.cleared_skips = 0
        self.reset_count = 0
        self.oom_options: list[OomRecoveryOptions] = []
        self.oom_recovery_reply: tuple[str, int | None, int | None] | None = None
        self.saved_profiles: list[tuple[int, int, str]] = []

    def clear_benchmark_skip(self) -> None:
        self.cleared_skips += 1
        self.skip_event.clear()

    async def choose_deploy_config(self, server) -> tuple[int, int]:
        return 32, server.max_model_len

    def reset_benchmark(self) -> None:
        self.reset_count += 1
        self.points = []

    def save_intermediate_profile(self, selected_max_num_seqs, selected_max_model_len, docker_command) -> int:
        self.saved_profiles.append((selected_max_num_seqs, selected_max_model_len, docker_command))
        return len(self.saved_profiles)

    async def choose_sweep_oom_recovery(self, options: OomRecoveryOptions):
        self.oom_options.append(options)
        if self.oom_recovery_reply is not None:
            return self.oom_recovery_reply
        return "deploy", options.max_num_seqs, options.max_model_len


def run_profile(profiler: LlmProfiler, booted: list[int]):
    """profile() against a fake server: no docker, no vLLM. `booted` records the --max-num-seqs each
    server was started with, in order."""

    async def fake_start(max_num_seqs: int, requested_max_model_len: int | None = None) -> ServerInfo:
        booted.append(max_num_seqs)
        # 2048 makes prompt_token_values() a two-series sweep: 1 and 1024 tokens.
        return ServerInfo(kv_token_size=100_000, max_model_len=2048, logs=[])

    profiler.start_vllm_instance = fake_start
    profiler.stop_vllm_docker = lambda: None

    async def scenario():
        try:
            return await profiler.profile()
        finally:
            await profiler.bench.aclose()

    return asyncio.run(scenario())


def test_profile_boots_one_server_per_max_num_seqs_value_and_tags_its_points():
    observer = ProfileObserver()
    profiler = make_profiler(observer)
    booted: list[int] = []

    async def fake_batch(num_seqs, n_tokens):
        return MEASUREMENT

    profiler.run_batch = fake_batch

    result = run_profile(profiler, booted)

    assert booted == MAX_NUM_SEQS_VALUES
    assert result.max_num_seqs_values == MAX_NUM_SEQS_VALUES
    # 2 prompt lengths x 8 concurrencies x 3 servers, and the bar ends full.
    assert observer.progress[-1] == (48, 48)
    # The concurrency swept at each server is the same regardless of what that server admits at once:
    # 128 requests against a --max-num-seqs=8 server is a point worth having.
    for max_num_seqs in MAX_NUM_SEQS_VALUES:
        swept = [point.concurrent_requests for point in result.bench_points if point.max_num_seqs == max_num_seqs]
        assert sorted(set(swept)) == [1, 2, 4, 8, 16, 32, 64, 128]


def test_profile_uses_the_benchmark_grid_from_its_config():
    observer = ProfileObserver()
    config = ProfilerConfig(
        model_name="test-model",
        max_num_seqs_values=[12, 3],
        concurrent_request_values=[5, 1],
    )
    profiler = make_profiler(observer, config)
    booted: list[int] = []

    async def fake_batch(num_seqs, n_tokens):
        return MEASUREMENT

    profiler.run_batch = fake_batch
    result = run_profile(profiler, booted)

    assert booted == [3, 12]
    assert result.max_num_seqs_values == [3, 12]
    assert result.concurrent_request_values == [1, 5]
    assert observer.progress[-1] == (8, 8)
    assert {point.concurrent_requests for point in result.bench_points} == {1, 5}


@pytest.mark.parametrize(
    ("field", "values"),
    [
        ("max_num_seqs_values", []),
        ("max_num_seqs_values", [0, 4]),
        ("concurrent_request_values", [1, 1]),
    ],
)
def test_profiler_config_rejects_invalid_benchmark_values(field, values):
    with pytest.raises(ValueError):
        ProfilerConfig(model_name="test-model", **{field: values})


def test_profiler_config_rejects_a_max_model_len_below_the_vllm_floor():
    config = ProfilerConfig(model_name="test-model", max_model_len=MIN_MAX_MODEL_LEN - 1)

    with pytest.raises(ValueError, match="at least"):
        validate_config(config)


def test_profiler_config_rejects_a_max_model_len_without_room_for_the_completion():
    config = ProfilerConfig(model_name="test-model", completion_tokens=4096, max_model_len=MIN_MAX_MODEL_LEN)

    with pytest.raises(ValueError, match="room for the completion"):
        validate_config(config)


def test_profile_boots_servers_at_the_configured_max_model_len():
    observer = ProfileObserver()
    config = ProfilerConfig(
        model_name="test-model", max_model_len=4096, max_num_seqs_values=[8], concurrent_request_values=[1]
    )
    profiler = make_profiler(observer, config)
    booted: list[int | None] = []

    async def fake_start(max_num_seqs: int, requested_max_model_len: int | None = None) -> ServerInfo:
        booted.append(requested_max_model_len)
        return ServerInfo(kv_token_size=100_000, max_model_len=requested_max_model_len or 8192, logs=[])

    async def fake_batch(num_seqs, n_tokens):
        return MEASUREMENT

    profiler.start_vllm_instance = fake_start
    profiler.run_batch = fake_batch
    profiler.stop_vllm_docker = lambda: None

    async def scenario():
        try:
            return await profiler.profile()
        finally:
            await profiler.bench.aclose()

    asyncio.run(scenario())

    # The user's cap reaches the server boot instead of vLLM's own maximum.
    assert booted == [4096]


def test_profile_stresses_every_server_at_full_context_before_sweeping():
    observer = ProfileObserver()
    profiler = make_profiler(observer)
    booted: list[int] = []
    stress_calls: list[tuple[int, int]] = []

    async def fake_batch(num_seqs, n_tokens):
        return MEASUREMENT

    async def fake_stress(max_num_seqs, max_model_len):
        stress_calls.append((max_num_seqs, max_model_len))

    profiler.run_batch = fake_batch
    profiler.run_full_context_stress = fake_stress
    run_profile(profiler, booted)

    assert stress_calls == [(max_num_seqs, 2048) for max_num_seqs in MAX_NUM_SEQS_VALUES]


def test_full_context_stress_fires_max_num_seqs_full_prompts_with_one_token(monkeypatch):
    observer = SkipObserver()
    calls: list[tuple] = []

    class FakeBench:
        completion_tokens = 1

        def __init__(self, base_url, api_key, ttft_timeout):
            calls.append(("init", ttft_timeout))
            self.client = object()

        async def build_prompts(self, model_name, num_seqs, n_tokens):
            calls.append(("prompts", model_name, num_seqs, n_tokens))
            return ["prompt"] * num_seqs

        async def run_batch(self, model_name, prompts, completion_tokens):
            calls.append(("batch", model_name, len(prompts), completion_tokens))
            return [SimpleNamespace(completion_tokens=self.completion_tokens, ttft=0.5)] * len(prompts)

        async def aclose(self):
            calls.append(("close",))

    monkeypatch.setattr("backend.llm_profiler.VllmBenchClient", FakeBench)
    profiler = LlmProfiler(ProfilerConfig(model_name="test-model", stress_test_timeout=180), "fake-container", observer)

    asyncio.run(profiler.run_full_context_stress(max_num_seqs=16, max_model_len=4096))

    assert ("init", 180) in calls
    assert ("prompts", "test-model", 16, 4095) in calls
    assert ("batch", "test-model", 16, 1) in calls
    assert observer.stress_tests[0].median_ttft == 0.5

    FakeBench.completion_tokens = 2
    with pytest.raises(RuntimeError, match="exactly one completion token"):
        asyncio.run(profiler.run_full_context_stress(max_num_seqs=16, max_model_len=4096))


def test_a_stress_test_timeout_names_the_max_num_seqs_it_was_testing(monkeypatch):
    class TimingOutBench:
        client = None

        def __init__(self, *args):
            pass

        async def build_prompts(self, model_name, num_seqs, n_tokens):
            return ["prompt"] * num_seqs

        async def run_batch(self, model_name, prompts, completion_tokens):
            raise APITimeoutError(request=Request("POST", "http://localhost"))

        async def aclose(self):
            pass

    monkeypatch.setattr("backend.llm_profiler.VllmBenchClient", TimingOutBench)
    profiler = LlmProfiler(
        ProfilerConfig(model_name="test-model", stress_test_timeout=180), "fake-container", ProfileObserver()
    )

    with pytest.raises(SweepCudaOutOfMemory) as exc_info:
        asyncio.run(profiler.run_full_context_stress(max_num_seqs=16, max_model_len=4096))

    assert exc_info.value.max_num_seqs == 16
    assert exc_info.value.reason == "stress_timeout"
    assert exc_info.value.detail == (
        "Full-context stress test timed out after 180 seconds at --max-num-seqs = 16."
    )


def test_skip_abandons_a_running_stress_test_without_waiting_for_its_timeout(monkeypatch):
    observer = SkipObserver()

    class HangingBench:
        client = None

        def __init__(self, *args):
            pass

        async def build_prompts(self, model_name, num_seqs, n_tokens):
            return ["prompt"] * num_seqs

        async def run_batch(self, model_name, prompts, completion_tokens):
            observer.skip_event.set()
            await asyncio.sleep(180)
            raise AssertionError("the stress test should not have run to its timeout")

        async def aclose(self):
            pass

    monkeypatch.setattr("backend.llm_profiler.VllmBenchClient", HangingBench)
    profiler = LlmProfiler(
        ProfilerConfig(model_name="test-model", stress_test_timeout=180), "fake-container", observer
    )

    async def scenario():
        with pytest.raises(BenchmarkSkipped):
            await asyncio.wait_for(profiler.run_full_context_stress(max_num_seqs=16, max_model_len=4096), timeout=5)

    asyncio.run(scenario())


def test_skipping_the_benchmark_drops_every_server_still_to_be_benchmarked():
    observer = ProfileObserver()
    profiler = make_profiler(observer)
    first_server, second_server = MAX_NUM_SEQS_VALUES[0], MAX_NUM_SEQS_VALUES[1]
    booted: list[int] = []

    async def fake_batch(num_seqs, n_tokens):
        # Skip requested while the second server's second point is in flight.
        if booted[-1] == second_server and num_seqs == 2:
            observer.skip_event.set()
            await asyncio.sleep(60)
        return MEASUREMENT

    profiler.run_batch = fake_batch
    result = run_profile(profiler, booted)

    # Skip fast-forwards to the deploy choice: the servers after the skipped one never boot.
    assert booted == [first_server, second_server]
    assert observer.cleared_skips == 1
    measured = [point.max_num_seqs for point in result.bench_points]
    # Everything measured before the skip is kept, including the point that landed on the skipped server.
    assert measured.count(first_server) == 16
    assert measured.count(second_server) == 1


def test_the_benchmark_is_only_skippable_once_a_server_has_been_benchmarked_end_to_end():
    observer = ProfileObserver()
    profiler = make_profiler(observer)
    booted: list[int] = []
    skippable_while_sweeping: list[bool] = []

    async def fake_batch(num_seqs, n_tokens):
        skippable_while_sweeping.append(observer.skippable)
        return MEASUREMENT

    profiler.run_batch = fake_batch
    run_profile(profiler, booted)

    # Nothing to deploy from during the first server's sweep, so the skip button is not offered yet.
    assert skippable_while_sweeping[:16] == [False] * 16
    assert all(skippable_while_sweeping[16:])


def test_skip_abandons_a_booting_server_without_waiting_for_it(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    observer = SkipObserver()
    profiler = make_profiler(observer)

    class NeverReadyProcess:
        stdout = iter(())

        def poll(self):
            return None

    monkeypatch.setattr("backend.llm_profiler.subprocess.Popen", lambda *args, **kwargs: NeverReadyProcess())

    async def never_ready(bench):
        return False

    monkeypatch.setattr("backend.llm_profiler.is_server_ready", never_ready)
    observer.skip_event.set()

    async def scenario():
        try:
            # A boot takes minutes and its timeout is config.timeout; the skip must not wait for either.
            with pytest.raises(BenchmarkSkipped):
                await asyncio.wait_for(profiler.run_vllm_instance(4, 2048), timeout=5)
        finally:
            await profiler.bench.aclose()

    asyncio.run(scenario())


def test_sweep_turns_a_generic_api_failure_into_recoverable_cuda_oom(monkeypatch):
    observer = SkipObserver()
    profiler = make_profiler(observer)

    async def failing_batch(num_seqs, n_tokens):
        raise RuntimeError("EngineCore encountered an issue")

    profiler.run_batch = failing_batch

    async def fake_to_thread(function, *args, **kwargs):
        assert function.__name__ == "container_logs"
        return VLLM_OOM_LOGS

    monkeypatch.setattr("backend.llm_profiler.asyncio.to_thread", fake_to_thread)

    async def scenario():
        try:
            with pytest.raises(SweepCudaOutOfMemory) as exc_info:
                await profiler.run_sweep(4, [1], [1024], 8192)
            return exc_info.value
        finally:
            await profiler.bench.aclose()

    assert asyncio.run(scenario()).server_max_model_len == 8192


def test_first_point_oom_restarts_at_half_context_without_prompting():
    observer = ProfileObserver()
    profiler = make_profiler(observer)
    booted: list[tuple[int, int | None]] = []

    async def fake_start(max_num_seqs: int, requested_max_model_len: int | None = None) -> ServerInfo:
        booted.append((max_num_seqs, requested_max_model_len))
        return ServerInfo(kv_token_size=100_000, max_model_len=requested_max_model_len or 8192, logs=[])

    async def fake_sweep(max_num_seqs, concurrent_values, token_values, server_max_model_len, *args):
        if server_max_model_len == 8192:
            raise SweepCudaOutOfMemory(server_max_model_len, max_num_seqs=max_num_seqs)
        point = BenchPoint(
            max_num_seqs=max_num_seqs,
            series_id="1 token",
            concurrent_requests=1,
            **MEASUREMENT.model_dump(),
        )
        return [point], False

    profiler.start_vllm_instance = fake_start
    profiler.run_sweep = fake_sweep
    profiler.stop_vllm_docker = lambda: None

    async def scenario():
        try:
            return await profiler.profile()
        finally:
            await profiler.bench.aclose()

    result = asyncio.run(scenario())

    assert booted[0] == (MAX_NUM_SEQS_VALUES[0], None)
    assert booted[1] == (MAX_NUM_SEQS_VALUES[0], 4096)
    assert observer.reset_count == 1
    assert observer.context_lengths == [(4096, "oom", MAX_NUM_SEQS_VALUES[0])]
    assert observer.oom_options == []
    assert result.selected_max_model_len == 4096


def test_first_full_context_stress_timeout_restarts_at_half_context_without_prompting():
    observer = ProfileObserver()
    profiler = make_profiler(observer)
    booted: list[tuple[int, int | None]] = []

    async def fake_start(max_num_seqs: int, requested_max_model_len: int | None = None) -> ServerInfo:
        booted.append((max_num_seqs, requested_max_model_len))
        return ServerInfo(kv_token_size=100_000, max_model_len=requested_max_model_len or 8192, logs=[])

    async def fake_stress(max_num_seqs, max_model_len):
        if max_model_len == 8192:
            raise SweepCudaOutOfMemory(
                max_model_len,
                detail=f"Full-context stress test timed out after 180 seconds at --max-num-seqs = {max_num_seqs}.",
                max_num_seqs=max_num_seqs,
                reason="stress_timeout",
            )

    async def fake_sweep(max_num_seqs, concurrent_values, token_values, server_max_model_len, *args):
        point = BenchPoint(
            max_num_seqs=max_num_seqs,
            series_id="1 token",
            concurrent_requests=1,
            **MEASUREMENT.model_dump(),
        )
        return [point], False

    profiler.start_vllm_instance = fake_start
    profiler.run_full_context_stress = fake_stress
    profiler.run_sweep = fake_sweep
    profiler.stop_vllm_docker = lambda: None

    async def scenario():
        try:
            return await profiler.profile()
        finally:
            await profiler.bench.aclose()

    result = asyncio.run(scenario())

    assert booted[0] == (MAX_NUM_SEQS_VALUES[0], None)
    assert booted[1] == (MAX_NUM_SEQS_VALUES[0], 4096)
    assert observer.reset_count == 1
    assert observer.context_lengths == [(4096, "stress_timeout", MAX_NUM_SEQS_VALUES[0])]
    # The backend words the warning the frontend renders, down to the hint that gets the context back.
    assert observer.context_length_messages == [
        f"Full-context stress test timed out after 180 seconds at --max-num-seqs = {MAX_NUM_SEQS_VALUES[0]}."
        " Restarting the benchmark with --max-model-len = 4,096."
        " To profile with the previous --max-model-len, restart profiling with a higher stress test timeout."
    ]
    assert observer.oom_options == []
    assert result.selected_max_model_len == 4096


def test_later_stress_timeout_defaults_to_the_last_fully_swept_server_context():
    observer = ProfileObserver()
    profiler = make_profiler(observer)

    async def fake_start(max_num_seqs: int, requested_max_model_len: int | None = None) -> ServerInfo:
        return ServerInfo(kv_token_size=100_000, max_model_len=8192, logs=[])

    async def fake_stress(max_num_seqs, max_model_len):
        if max_num_seqs == 16:
            raise SweepCudaOutOfMemory(
                max_model_len,
                detail=f"Full-context stress test timed out after 180 seconds at --max-num-seqs = {max_num_seqs}.",
                max_num_seqs=max_num_seqs,
                reason="stress_timeout",
            )
        return StressTestResult(max_num_seqs=max_num_seqs, max_model_len=max_model_len, median_ttft=0.5)

    async def fake_sweep(max_num_seqs, concurrent_values, token_values, server_max_model_len, *args):
        return [BenchPoint(max_num_seqs=max_num_seqs, series_id="1 token", concurrent_requests=1, **MEASUREMENT.model_dump())], False

    profiler.start_vllm_instance = fake_start
    profiler.run_full_context_stress = fake_stress
    profiler.run_sweep = fake_sweep
    profiler.stop_vllm_docker = lambda: None

    async def scenario():
        try:
            return await profiler.profile()
        finally:
            await profiler.bench.aclose()

    result = asyncio.run(scenario())

    assert observer.oom_options == [
        OomRecoveryOptions(
            max_num_seqs=4,
            max_model_len=8192,
            retry_max_model_len=4096,
            failure_detail="Full-context stress test timed out after 180 seconds at --max-num-seqs = 16.",
            # --max-num-seqs = 4 was swept end to end before 16 timed out, so its bench can be saved.
            savable=True,
        )
    ]
    assert (result.selected_max_num_seqs, result.selected_max_model_len) == (4, 8192)


def test_save_and_retry_saves_the_completed_bench_then_restarts_smaller():
    observer = ProfileObserver()
    observer.oom_recovery_reply = ("save_retry", 4, 8192)
    profiler = make_profiler(observer)

    async def fake_start(max_num_seqs: int, requested_max_model_len: int | None = None) -> ServerInfo:
        return ServerInfo(kv_token_size=100_000, max_model_len=requested_max_model_len or 8192, logs=[])

    async def fake_stress(max_num_seqs, max_model_len):
        # The full-context stress test only times out on the first pass, at the larger context.
        if max_num_seqs == 16 and max_model_len == 8192:
            raise SweepCudaOutOfMemory(
                max_model_len,
                detail="Full-context stress test timed out after 180 seconds at --max-num-seqs = 16.",
                max_num_seqs=max_num_seqs,
                reason="stress_timeout",
            )
        return StressTestResult(max_num_seqs=max_num_seqs, max_model_len=max_model_len, median_ttft=0.5)

    async def fake_sweep(max_num_seqs, concurrent_values, token_values, server_max_model_len, *args):
        return [BenchPoint(max_num_seqs=max_num_seqs, series_id="1 token", concurrent_requests=1, **MEASUREMENT.model_dump())], False

    profiler.start_vllm_instance = fake_start
    profiler.run_full_context_stress = fake_stress
    profiler.run_sweep = fake_sweep
    profiler.stop_vllm_docker = lambda: None

    async def scenario():
        try:
            return await profiler.profile()
        finally:
            await profiler.bench.aclose()

    result = asyncio.run(scenario())

    assert observer.oom_options[0].savable is True
    # The proven bench was saved once, with the values the user submitted, before the restart wiped it.
    assert len(observer.saved_profiles) == 1
    saved_max_num_seqs, saved_max_model_len, saved_command = observer.saved_profiles[0]
    assert (saved_max_num_seqs, saved_max_model_len) == (4, 8192)
    assert "8192" in saved_command
    assert observer.reset_count == 1
    # The retry ran a fresh sweep at the halved context, and that is what the job finishes on.
    assert (result.selected_max_num_seqs, result.selected_max_model_len) == (32, 4096)


def test_first_point_oom_errors_after_the_configured_number_of_halving_retries():
    observer = ProfileObserver()
    profiler = make_profiler(observer)
    booted: list[int | None] = []

    async def fake_start(max_num_seqs: int, requested_max_model_len: int | None = None) -> ServerInfo:
        booted.append(requested_max_model_len)
        return ServerInfo(kv_token_size=100_000, max_model_len=requested_max_model_len or 8192, logs=[])

    async def always_oom(*args):
        raise SweepCudaOutOfMemory(args[3])

    profiler.start_vllm_instance = fake_start
    profiler.run_sweep = always_oom
    profiler.stop_vllm_docker = lambda: None

    async def scenario():
        try:
            with pytest.raises(RuntimeError, match="before the first benchmark point"):
                await profiler.profile()
        finally:
            await profiler.bench.aclose()

    asyncio.run(scenario())

    assert booted == [None, 4096, 2048]
    assert observer.reset_count == MAX_SWEEP_OOM_MAX_MODEL_LEN_HALVING_RETRIES


def test_oom_after_a_working_point_offers_bounded_deployment_instead_of_an_automatic_retry():
    observer = ProfileObserver()
    profiler = make_profiler(observer)

    async def fake_start(max_num_seqs: int, requested_max_model_len: int | None = None) -> ServerInfo:
        return ServerInfo(kv_token_size=100_000, max_model_len=8192, logs=[])

    async def fake_sweep(max_num_seqs, concurrent_values, token_values, server_max_model_len, *args):
        point = BenchPoint(
            max_num_seqs=max_num_seqs,
            series_id="1024 tokens",
            concurrent_requests=1,
            **MEASUREMENT.model_dump(),
        )
        # This is the real shape: run_sweep has a point from the current server, then a later
        # request crashes EngineCore before the method can return its partial list.
        raise SweepCudaOutOfMemory(server_max_model_len, [point])

    profiler.start_vllm_instance = fake_start
    profiler.run_sweep = fake_sweep
    profiler.stop_vllm_docker = lambda: None

    async def scenario():
        try:
            return await profiler.profile()
        finally:
            await profiler.bench.aclose()

    result = asyncio.run(scenario())

    assert observer.reset_count == 0
    assert observer.oom_options == [
        OomRecoveryOptions(max_num_seqs=4, max_model_len=1024 + DEFAULT_COMPLETION_TOKENS, retry_max_model_len=4096)
    ]
    assert (result.selected_max_num_seqs, result.selected_max_model_len) == (4, 1024 + DEFAULT_COMPLETION_TOKENS)


# A crash dump shaped like vLLM's: the exception that killed the engine is buried under the teardown
# noise that follows it, and every line is prefixed with the worker that printed it.
VLLM_OOM_LOGS = """
INFO 07-13 03:44:10 [api_server.py:1] vLLM API server version 0.25.0
INFO 07-13 03:44:12 [gpu_worker.py:298] GPU KV cache size: 212,507 tokens
(EngineCore_DP0 pid=131) ERROR 07-13 03:51:02 [core.py:770] EngineCore encountered a fatal error.
(EngineCore_DP0 pid=131) ERROR 07-13 03:51:02 [core.py:770] Traceback (most recent call last):
(EngineCore_DP0 pid=131) ERROR 07-13 03:51:02 [core.py:770]   File "/usr/lib/vllm/v1/engine/core.py", line 761, in run_engine_core
(EngineCore_DP0 pid=131) ERROR 07-13 03:51:02 [core.py:770]     engine_core.run_busy_loop()
(EngineCore_DP0 pid=131) ERROR 07-13 03:51:02 [core.py:770] torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.00 GiB. GPU 0 has a total capacity of 22.06 GiB
(EngineCore_DP0 pid=131) Process EngineCore_DP0:
INFO 07-13 03:51:03 [async_llm.py:400] Engine core shut down.
INFO:     127.0.0.1:52170 - "POST /v1/chat/completions HTTP/1.1" 500 Internal Server Error
"""


def test_the_root_cause_is_the_engines_own_exception_not_the_noise_after_it():
    assert container_root_cause(VLLM_OOM_LOGS) == (
        "torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.00 GiB. "
        "GPU 0 has a total capacity of 22.06 GiB"
    )


def test_logs_with_no_exception_have_no_root_cause():
    assert container_root_cause("INFO 07-13 03:44:10 [api_server.py:1] Starting vLLM\n") == ""


# The shape a real OOM takes: the engine dies, then keeps printing. The exception that killed it is far
# up, and the ones after it — the death, then a failure per in-flight request — are its consequences.
VLLM_OOM_THEN_FALLOUT = """
(EngineCore_DP0 pid=131) ERROR 07-13 03:51:02 [core.py:770] torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.00 GiB
(EngineCore_DP0 pid=131) Process EngineCore_DP0:
ERROR 07-13 03:51:03 [async_llm.py:420] AsyncLLM output_handler failed.
ERROR 07-13 03:51:03 [async_llm.py:420] vllm.v1.engine.exceptions.EngineDeadError: EngineCore encountered an issue.
INFO:     127.0.0.1:52170 - "POST /v1/chat/completions HTTP/1.1" 500 Internal Server Error
ERROR 07-13 03:51:04 [serving_chat.py:99] ValueError: Engine is dead
"""


def test_a_fatal_oom_outranks_the_exceptions_it_causes_further_down():
    # Taking the last exception would report "ValueError: Engine is dead", which says nothing.
    assert container_root_cause(VLLM_OOM_THEN_FALLOUT) == (
        "torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.00 GiB"
    )
