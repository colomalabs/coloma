import asyncio
import contextlib
import os
import re
import shlex
import statistics
import subprocess
import threading
from collections import deque
from collections.abc import Coroutine
from time import perf_counter, time
from typing import Any, Literal, Protocol

from httpx import ReadTimeout
from openai import APIConnectionError, APITimeoutError, NotFoundError
from openai.types.chat import ChatCompletionUserMessageParam
from pydantic import BaseModel, Field, field_validator

from backend.vllm_bench_client import VllmBenchClient

PROFILER_HF_HOME = "~/.cache/huggingface"
PROFILER_VLLM_HOME = "~/.cache/vllm"
CONTAINER_PREFIX = "coloma-profiler"
DEFAULT_VLLM_IMAGE = "vllm/vllm-openai:v0.25.1"
DEFAULT_VLLM_API_KEY = "ABC"
# Host port the profiled/deployed vLLM container is published on (container side is
# always 8000, vLLM's default).
VLLM_HOST_PORT = 8000


def vllm_base_url(port: int) -> str:
    return f"http://localhost:{port}"

# The server is booted once per value, and swept once per boot. User-provided values are normalized
# to this same ascending order so progress, chart groups, and persisted profiles remain predictable.
MAX_NUM_SEQS_VALUES = [1, 4, 16]
# What the sweep fires at each server, independent of what that server was booted with: 128 requests
# against a server that decodes 8 at a time is a valid point — the other 120 just queue.
CONCURRENT_REQUEST_VALUES = [1, 2, 4, 8, 16, 32, 64, 128]
MAX_MODEL_LEN_ESTIMATION_RETRIES = 2
# A request-time OOM is different from vLLM's startup estimate being slightly optimistic above: no
# benchmark point exists to guide the user yet, so retry the *whole* sweep at half context twice before
# surfacing a terminal error.
MAX_SWEEP_OOM_MAX_MODEL_LEN_HALVING_RETRIES = 2
# Long enough that the mean ITL amortizes the slow tokens a request emits while its batch-mates
# prefill, instead of measuring only that transient — 10 tokens made every --max-num-seqs look alike
# because the sweep never reached the decode regime where the knob matters.
DEFAULT_COMPLETION_TOKENS = 64
MAX_COMPLETION_TOKENS = 4096
# How long a request may go without receiving a token before it is given up on.
DEFAULT_TTFT_TIMEOUT = 30
DEFAULT_STRESS_TEST_TIMEOUT = 180


class ProfilerConfig(BaseModel):
    model_name: str
    api_key: str = DEFAULT_VLLM_API_KEY
    port: int = VLLM_HOST_PORT
    image_tag: str = DEFAULT_VLLM_IMAGE
    fp8: bool = True
    gpu_mem: float = 0.95
    hf_home: str = PROFILER_HF_HOME
    vllm_home: str = PROFILER_VLLM_HOME
    extra_vllm_args: str = ""
    timeout: int = 600
    ttft_timeout: int = DEFAULT_TTFT_TIMEOUT
    stress_test_timeout: int = DEFAULT_STRESS_TEST_TIMEOUT
    # How many tokens every sweep request decodes. Prefill-heavy jobs can lower it, decode-heavy
    # jobs (long answers) should raise it toward their real completion length.
    completion_tokens: int = DEFAULT_COMPLETION_TOKENS
    # None lets vLLM pick the model's own maximum context length; a value boots every server capped
    # at it from the start instead of waiting for an OOM to force it down.
    max_model_len: int | None = None
    max_num_seqs_values: list[int] = Field(default_factory=lambda: list(MAX_NUM_SEQS_VALUES))
    concurrent_request_values: list[int] = Field(default_factory=lambda: list(CONCURRENT_REQUEST_VALUES))

    @field_validator("max_num_seqs_values", "concurrent_request_values")
    @classmethod
    def normalize_benchmark_values(cls, values: list[int]) -> list[int]:
        # A canonical order makes persisted profiles and chart groups predictable. Reject duplicates
        # instead of silently changing the grid the user submitted.
        if not values:
            raise ValueError("must contain at least one value")
        if any(value < 1 for value in values):
            raise ValueError("must contain only positive integers")
        if len(values) != len(set(values)):
            raise ValueError("must not contain duplicate values")
        if len(values) > 32:
            raise ValueError("must contain at most 32 values")
        return sorted(values)


class BenchMeasurement(BaseModel):
    """What one batch fired at the server measured."""
    median_prompt_tokens: int
    # Tokens each request decoded (median), so charts can label what the felt metrics amortize over.
    completion_tokens: int
    # Wall clock of the whole batch, first request sent to last one finished: the job time the
    # calculator validates itself against.
    duration: float
    # Felt TTFT: the wait includes queueing behind the other requests of the batch.
    median_ttft: float
    # Felt ITL: mean token gap over the whole response, slow start under co-resident prefill included.
    average_itl: float
    system_throughput: float


class BenchPoint(BenchMeasurement):
    """A measurement placed on the sweep grid: max_num_seqs is what the server serving it was booted
    with, series_id is the prompt length, concurrent_requests the concurrency it was fired at."""
    max_num_seqs: int
    series_id: str
    concurrent_requests: int


class StressTestResult(BaseModel):
    max_num_seqs: int
    max_model_len: int
    median_ttft: float


class ServerInfo(BaseModel):
    """What the running server tells us about itself, and what the deployment is bounded by."""
    # KV cache size in tokens: the total token budget shared by all resident sequences.
    kv_token_size: int
    # The context length the server actually booted with, which is the model's own maximum unless
    # it had to be capped to fit in memory.
    max_model_len: int
    logs: list[str]


class ProfileResult(BaseModel):
    concurrent_request_values: list[int]
    max_num_seqs_values: list[int]
    bench_points: list[BenchPoint]
    docker_command: str
    selected_max_num_seqs: int
    selected_max_model_len: int


class OomRecoveryOptions(BaseModel):
    """The suggested successful point and retry context shown after a capacity failure."""
    max_num_seqs: int
    max_model_len: int
    retry_max_model_len: int
    failure_detail: str = "CUDA ran out of memory during the benchmark."
    # True once a --max-num-seqs finished end to end: its bench is worth saving as a profile before a
    # retry drops it, so the frontend offers "save and retry" rather than a bare "retry".
    savable: bool = False


class SweepCudaOutOfMemory(RuntimeError):
    """A benchmark request killed EngineCore with CUDA OOM, rather than merely timing out."""

    def __init__(
            self, server_max_model_len: int, bench_points: list[BenchPoint] | None = None,
            detail: str = "CUDA ran out of memory during the benchmark.",
            max_num_seqs: int | None = None,
            reason: Literal["oom", "stress_timeout"] = "oom",
    ):
        super().__init__(detail)
        self.server_max_model_len = server_max_model_len
        self.detail = detail
        # What the server ran out of: memory, or patience during the full-context stress test. The
        # frontend words its warning from this, so it must not be re-derived from `detail`'s prose.
        self.reason = reason
        # The --max-num-seqs the sweep was on when it failed: the warnings name it so the user knows
        # which server batch size the cap and the recovery options belong to.
        self.max_num_seqs = max_num_seqs
        # run_sweep has already published these to the observer, but profile() also needs them to
        # decide between the interactive recovery path and automatic halving.
        self.bench_points = bench_points or []


class BenchmarkSkipped(Exception):
    """The user asked to stop benchmarking and go straight to the deploy choice. Raised from wherever the
    profiler happened to be — booting a server, stress-testing it, or sweeping it — so no phase has to run
    to completion before the skip takes effect."""


class StepObserver(Protocol):
    def is_cancelled(self) -> bool: ...

    def is_benchmark_skipped(self) -> bool: ...

    async def wait_benchmark_skipped(self) -> None: ...

    def clear_benchmark_skip(self) -> None: ...

    def set_process(self, proc: subprocess.Popen[str] | None) -> None: ...

    def add_bench_point(self, point: BenchPoint) -> None: ...

    def add_stress_test(self, result: StressTestResult) -> None: ...

    def set_benchmark_timeout(self, num_seqs: int) -> None: ...

    def set_benchmark_skippable(self, skippable: bool) -> None: ...

    def set_benchmark_progress(self, done: int, total: int) -> None: ...

    def set_context_length_capped(
            self,
            capped_max_model_len: int,
            reason: Literal["oom", "stress_timeout"] = "oom",
            max_num_seqs: int | None = None,
            message: str = "",
    ) -> None: ...

    def reset_benchmark(self) -> None: ...

    def save_intermediate_profile(
            self, selected_max_num_seqs: int, selected_max_model_len: int, docker_command: str
    ) -> int: ...

    async def choose_sweep_oom_recovery(
            self, options: OomRecoveryOptions
    ) -> tuple[Literal["deploy", "retry", "save_retry"], int | None, int | None]: ...

    async def choose_deploy_config(self, server: ServerInfo) -> tuple[int, int]: ...

    def update_step(
            self,
            step_id: int,
            status: Literal["pending", "running", "done", "error", "skipped", "cancelled"] | None = None,
            detail: str | None = None,
            result: dict[str, Any] | None = None,
            logs: list[str] | None = None,
            error: str | None = None,
    ) -> None: ...


# Starting the server is part of the benchmark step, not a step of its own.
STEP_TITLES = ["Download", "Benchmark", "Configure concurrency"]
STEP_DOWNLOAD, STEP_BENCHMARK, STEP_CONFIGURE = 1, 2, 3


def validate_config(config: ProfilerConfig) -> None:
    if not config.model_name.strip():
        raise ValueError("model_name is required")
    if config.gpu_mem <= 0 or config.gpu_mem > 1:
        raise ValueError("gpu_mem must be in (0, 1]")
    if config.ttft_timeout < 1 or config.stress_test_timeout < 1:
        raise ValueError("TTFT timeouts must be positive")
    # At least 2 so every request has a token gap to measure; mean ITL is meaningless below that.
    if config.completion_tokens < 2 or config.completion_tokens > MAX_COMPLETION_TOKENS:
        raise ValueError(f"completion_tokens must be between 2 and {MAX_COMPLETION_TOKENS}")
    if config.max_model_len is not None:
        if config.max_model_len <= config.completion_tokens:
            raise ValueError("max_model_len must leave room for the completion tokens")
    if config.port < 1 or config.port > 65_535:
        raise ValueError("port must be between 1 and 65535")
    for raw in (config.hf_home, config.vllm_home):
        if not raw.strip():
            raise ValueError("Profiler cache paths are required")


DEPLOY_CONTAINER_NAME = "coloma-deploy"
# The container's whole output, not a tail: when the engine dies it keeps printing (worker teardown,
# the EngineCore trace, an API error per in-flight request), and the exception that actually killed it
# — a CUDA OOM, say — scrolls far up. Only the sheer size is bounded, and from the end, since a job
# that ran a long sweep before dying can have logged a lot.
CONTAINER_LOG_MAX_CHARS = 400_000

# The bit of a line that names an exception, e.g. "torch.OutOfMemoryError: CUDA out of memory. Tried to
# allocate 2.00 GiB". Anything before it is skipped: vLLM prefixes its lines with the worker and pid
# ("(EngineCore_DP0 pid=123) ERROR ..."), so the exception is rarely at the start of the line.
_EXCEPTION_LINE = re.compile(r"^.*?(\S*(?:Error|Exception)(?:\[[^]]*])?: \S.*)$", re.MULTILINE)

# What actually kills the engine. These win over any other exception in the log, however far up they are:
# once the engine is dead it keeps printing — the death itself (EngineDeadError), then a failure for every
# request still in flight — so the *last* exception is fallout, and the cause is buried above it.
_FATAL_SIGNATURES = (
    "OutOfMemoryError",
    "CUDA out of memory",
    "No available memory for the cache blocks",
)


def is_cuda_out_of_memory(logs: str) -> bool:
    """Only treat the concrete CUDA allocator failure as recoverable sweep OOM.

    Other fatal signatures (for example no cache blocks during startup) need their original error
    reporting; retrying them blindly would hide a configuration problem.
    """
    return "CUDA out of memory" in logs


def build_deploy_cmd(config: ProfilerConfig, max_num_seqs: int, max_model_len: int | None,
                     *, ensure_cache_dirs: bool = True) -> list[str]:
    """The command that launches the deployment: the profiled server, detached and restarting on its own.
    Built once, when the profile is saved, and stored with it — see the note on ProfilerArtifact."""
    cmd = LlmProfiler.build_docker_cmd(
        config, DEPLOY_CONTAINER_NAME, max_num_seqs, max_model_len, ensure_cache_dirs=ensure_cache_dirs
    )
    return [cmd[0], cmd[1], "-d", "--restart", "unless-stopped", *cmd[2:]]


def container_logs(container_name: str, max_chars: int = CONTAINER_LOG_MAX_CHARS) -> str:
    """The vLLM container's whole output. When the profiler fails it is usually the engine that died, and
    its stack trace is inside the container — our traceback only ever gets to see the API error that came
    back over the wire ("EngineCore encountered an issue. See stack trace (above)"). Returns "" if the
    container is already gone, so this must be read before the container is removed."""
    try:
        proc = subprocess.run(
            ["docker", "logs", container_name],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError):
        return ""
    if proc.returncode != 0:
        return ""
    # vLLM logs to stderr, but a crash dump can land on either.
    logs = f"{proc.stdout}{proc.stderr}".strip()
    if len(logs) <= max_chars:
        return logs
    # Cut from the front: whatever killed the engine is at the end, and the startup banner is not what
    # anyone is scrolling for.
    return f"[... {len(logs) - max_chars} characters of earlier logs omitted ...]\n{logs[-max_chars:]}"


def container_root_cause(logs: str) -> str:
    """What killed the container, e.g. "torch.OutOfMemoryError: CUDA out of memory".

    It is what the profiler's own traceback cannot say — that one ends on the API error the dying engine
    returned. Hoisted to the top of the report so nobody has to scroll a crash dump to find it.

    A known-fatal exception wins wherever it appears, because the engine goes on printing after it and the
    trailing exceptions are consequences. Failing that, the last exception is the best guess available."""
    matches = [match.strip() for match in _EXCEPTION_LINE.findall(logs)]
    if not matches:
        return ""
    for match in matches:
        if any(signature in match for signature in _FATAL_SIGNATURES):
            return match
    return matches[-1]


def stop_named_containers(job_id: str) -> None:
    with contextlib.suppress(Exception):
        subprocess.run(["docker", "rm", "-f", f"{CONTAINER_PREFIX}-{job_id}"], capture_output=True, timeout=30)


async def is_server_ready(bench: VllmBenchClient) -> bool:
    """Return whether vLLM can complete a real OpenAI-compatible request."""
    try:
        model_name = await bench.get_model_name()
        res = await bench.client.chat.completions.create(
            model=model_name,
            messages=[ChatCompletionUserMessageParam(role="user", content="Hi")],
            max_completion_tokens=16,
            temperature=0,
        )
        if res.usage.completion_tokens == 0:
            raise RuntimeError("vLLM server generated 0 tokens during warmup.")
        return True
    except (APITimeoutError, APIConnectionError, NotFoundError):
        return False


class LlmProfiler:
    def __init__(self, config: ProfilerConfig, container_name: str, observer: StepObserver):
        self.config = config
        self.container_name = container_name
        self.observer = observer
        self.bench = VllmBenchClient(vllm_base_url(config.port), config.api_key, config.ttft_timeout)
        self.client = self.bench.client
        # The last words of the container we most recently tore down. A failing sweep unwinds through
        # the teardown in profile(), which removes the container: read after that, `docker logs` has
        # nothing left to give, and vLLM's own stack trace — the actual root cause — is lost.
        self.last_container_logs = ""

    def _check_cancelled(self) -> None:
        if self.observer.is_cancelled():
            raise asyncio.CancelledError()

    @staticmethod
    def _cache_path(raw: str) -> str:
        return os.path.abspath(os.path.expanduser(raw))

    def _model_cached(self) -> tuple[str, bool]:
        download_dir = os.path.join(self._cache_path(self.config.hf_home), "hub")
        model_cache_dir = os.path.join(
            download_dir,
            "models--" + self.config.model_name.replace("/", "--"),
            "snapshots",
        )
        return download_dir, os.path.isdir(model_cache_dir) and bool(os.listdir(model_cache_dir))

    async def download_model(self) -> None:
        self._check_cancelled()
        download_dir, cached = self._model_cached()
        if cached:
            self.observer.update_step(STEP_DOWNLOAD, "done",
                                      detail=f"Model {self.config.model_name} already in cache, skipping download.")
            return
        self.observer.update_step(STEP_DOWNLOAD, "running",
                                  detail=f"Downloading {self.config.model_name} into {download_dir}...")
        proc = subprocess.Popen(
            ["hf", "download", self.config.model_name, "--cache-dir", download_dir],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.observer.set_process(proc)
        output_tail = ""

        def _drain_output() -> None:
            nonlocal output_tail
            if proc.stdout is None:
                return
            while chunk := proc.stdout.read(4096):
                output_tail = (output_tail + chunk)[-4000:]

        drain_thread = threading.Thread(target=_drain_output, daemon=True)
        drain_thread.start()
        try:
            while proc.poll() is None:
                self._check_cancelled()
                await asyncio.sleep(1)
        finally:
            self.observer.set_process(None)
        drain_thread.join(timeout=5)
        if proc.returncode != 0:
            raise RuntimeError(f"Model download failed:\n{output_tail}")
        self.observer.update_step(STEP_DOWNLOAD, "done", detail=f"Downloaded {self.config.model_name}")

    def stop_vllm_docker(self) -> None:
        # Keep the logs before the container takes them with it: this runs on the failure path too.
        self.last_container_logs = container_logs(self.container_name) or self.last_container_logs
        try:
            subprocess.run(["docker", "rm", "-f", self.container_name], capture_output=True, timeout=30)
        except subprocess.TimeoutExpired:
            pass
        self.observer.set_process(None)

    @staticmethod
    def pretty_docker_cmd(cmd: list[str]) -> str:
        parts = ["docker run"]
        i = 2
        while i < len(cmd):
            if cmd[i].startswith("-") and "=" not in cmd[i] and i + 1 < len(cmd) and not cmd[i + 1].startswith("-"):
                val = cmd[i + 1]
                if " " in val or "{" in val:
                    val = f"'{val}'"
                parts.append(f"{cmd[i]} {val}")
                i += 2
            else:
                parts.append(cmd[i])
                i += 1
        return " \\\n    ".join(parts)

    @staticmethod
    def ensure_cache_dirs(config: ProfilerConfig) -> None:
        """Create the cache dirs the container mounts. Docker would otherwise create the missing ones
        itself, owned by root."""
        for raw in (config.hf_home, config.vllm_home):
            os.makedirs(LlmProfiler._cache_path(raw), exist_ok=True)

    @staticmethod
    def build_docker_cmd(config: ProfilerConfig, container_name: str, max_num_seqs: int, max_model_len: int | None,
                         *, ensure_cache_dirs: bool = True) -> list[str]:
        vllm_home = LlmProfiler._cache_path(config.vllm_home)
        if ensure_cache_dirs:
            os.makedirs(vllm_home, exist_ok=True)
        hf_home = LlmProfiler._cache_path(config.hf_home)
        if ensure_cache_dirs:
            os.makedirs(hf_home, exist_ok=True)
        cmd = ["docker", "run", "--runtime", "nvidia", "--gpus", "device=0", "--name", container_name]

        cmd.extend([
            "-v", f"{hf_home}:/root/.cache/huggingface",
            "-v", f"{vllm_home}:/root/.cache/vllm",
            "-e", "HF_HUB_OFFLINE=1",
            "-p", f"{config.port}:8000",
            config.image_tag,
            config.model_name,
            "--api-key", config.api_key,
            "--gpu-memory-utilization", str(config.gpu_mem),
            "--max-num-seqs", str(max_num_seqs),
            "--enable-prefix-caching",
        ])

        if config.fp8:
            cmd.extend(["--kv-cache-dtype", "fp8", "--quantization", "fp8"])

        if config.extra_vllm_args:
            cmd.extend(shlex.split(config.extra_vllm_args))

        if max_model_len is not None:
            cmd.extend(["--max-model-len", str(max_model_len)])

        return cmd

    async def run_vllm_instance(self, max_num_seqs: int, max_model_len: int | None) -> list[str]:
        self._check_cancelled()
        cmd = LlmProfiler.build_docker_cmd(self.config, self.container_name, max_num_seqs, max_model_len)
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            self.observer.set_process(proc)

            log_buffer = deque(maxlen=200)

            def _drain() -> None:
                if proc.stdout is None:
                    return
                for line in proc.stdout:
                    log_buffer.append(line)

            drain_thread = threading.Thread(target=_drain, daemon=True)
            drain_thread.start()

            start_time = time()
            while True:
                self._check_cancelled()
                # A boot takes minutes; the skip button must not have to wait it out.
                if self.observer.is_benchmark_skipped():
                    raise BenchmarkSkipped()
                if proc.poll() is not None:
                    drain_thread.join(timeout=5)
                    raise RuntimeError(f"Container crashed during startup:\n{''.join(log_buffer)}")

                if await is_server_ready(self.bench):
                    return list(log_buffer)

                if (time() - start_time) > self.config.timeout:
                    raise RuntimeError(
                        f"Container could not start under {self.config.timeout} seconds:\n{''.join(log_buffer)}"
                    )

                await asyncio.sleep(1)
        except RuntimeError as e:
            known_errors = [
                "CUDA out of memory.",
                "No available memory for the cache blocks.",
            ]
            if any(known_error in str(e) for known_error in known_errors):
                raise RuntimeError("This model is too big to fit in memory.") from e
            raise

    async def run_batch(self, num_seqs: int, n_tokens: int) -> BenchMeasurement:
        prompts = await self.bench.build_prompts(self.config.model_name, num_seqs, n_tokens)
        start = perf_counter()
        samples = await self.bench.run_batch(self.config.model_name, prompts, self.config.completion_tokens)
        delta = perf_counter() - start

        return BenchMeasurement(
            median_prompt_tokens=round(statistics.median(sample.prompt_tokens for sample in samples)),
            completion_tokens=round(statistics.median(sample.completion_tokens for sample in samples)),
            duration=delta,
            median_ttft=statistics.median(sample.ttft for sample in samples),
            average_itl=statistics.mean(sample.mean_itl for sample in samples),
            system_throughput=sum(sample.completion_tokens for sample in samples) / delta,
        )

    async def run_full_context_stress(self, max_num_seqs: int, max_model_len: int) -> StressTestResult:
        """Prove the server can admit a full context for every sequence before the lighter sweep."""
        self.observer.update_step(
            STEP_BENCHMARK,
            "running",
            detail=(
                f"Stress-testing {max_num_seqs} concurrent full-context requests with "
                f"--max-num-seqs = {max_num_seqs}."
            ),
        )
        stress_bench = VllmBenchClient(
            vllm_base_url(self.config.port), self.config.api_key, self.config.stress_test_timeout
        )
        try:
            prompts = await stress_bench.build_prompts(self.config.model_name, max_num_seqs, max_model_len - 1)
            # The stress test can hold the full timeout; race it against the skip rather than make the
            # user wait it out.
            samples = await self._run_or_skip(
                stress_bench.run_batch(self.config.model_name, prompts, completion_tokens=1)
            )
        except (ReadTimeout, APITimeoutError) as exc:
            raise SweepCudaOutOfMemory(
                max_model_len,
                detail=(
                    f"Full-context stress test timed out after {self.config.stress_test_timeout} seconds "
                    f"at --max-num-seqs = {max_num_seqs}."
                ),
                max_num_seqs=max_num_seqs,
                reason="stress_timeout",
            ) from exc
        except BenchmarkSkipped:
            # A skip is the user talking, not the server failing: no container logs to blame it on.
            raise
        except Exception as exc:
            logs = await asyncio.to_thread(container_logs, self.container_name)
            if is_cuda_out_of_memory(logs):
                self.last_container_logs = logs or self.last_container_logs
                raise SweepCudaOutOfMemory(
                    max_model_len,
                    detail=(
                        "CUDA ran out of memory during the full-context stress test at "
                        f"--max-num-seqs = {max_num_seqs}."
                    ),
                    max_num_seqs=max_num_seqs,
                ) from exc
            raise
        else:
            if len(samples) != max_num_seqs or any(sample.completion_tokens != 1 for sample in samples):
                raise RuntimeError("Full-context stress test expected exactly one completion token per request.")
            result = StressTestResult(
                max_num_seqs=max_num_seqs,
                max_model_len=max_model_len,
                median_ttft=statistics.median(sample.ttft for sample in samples),
            )
            self.observer.add_stress_test(result)
            return result
        finally:
            await stress_bench.aclose()

    async def _run_or_skip[T](self, coro: Coroutine[Any, Any, T]) -> T:
        """Await `coro`, abandoning the requests it has in flight as soon as the user skips. Raises
        BenchmarkSkipped in that case: a batch at high concurrency can take minutes, and waiting for it
        would make the skip button feel dead."""
        work = asyncio.ensure_future(coro)
        skip = asyncio.ensure_future(self.observer.wait_benchmark_skipped())
        try:
            done, _ = await asyncio.wait({work, skip}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            if not work.done():
                work.cancel()
            skip.cancel()
        if work in done:
            return work.result()
        with contextlib.suppress(asyncio.CancelledError):
            await work
        raise BenchmarkSkipped()

    async def run_prompt_batch_or_skip(self, num_seqs: int, n_tokens: int) -> BenchMeasurement | None:
        """Run one sweep point. Returns None when the user skipped and the point was abandoned."""
        try:
            return await self._run_or_skip(self.run_batch(num_seqs, n_tokens))
        except BenchmarkSkipped:
            return None

    async def start_vllm_instance(self, max_num_seqs: int, requested_max_model_len: int | None = None) -> ServerInfo:
        """Start the server at the given max-num-seqs and report what it booted with: the KV cache budget
        and the context length it settled on. vLLM's own token budget estimation can be a bit high and
        OOM, so it might need to be restarted up to two times."""
        self.observer.update_step(
            STEP_BENCHMARK, "running", detail=f"Starting vLLM server with --max-num-seqs = {max_num_seqs}."
        )
        # None lets vLLM pick the model's own maximum; a retry re-boots at the length it told us
        # actually fits.
        max_model_len = requested_max_model_len
        for attempt in range(MAX_MODEL_LEN_ESTIMATION_RETRIES + 1):
            try:
                logs = await self.run_vllm_instance(max_num_seqs, max_model_len)
                return ServerInfo(
                    kv_token_size=self.parse_kv_token_size(logs),
                    max_model_len=await self.bench.get_max_model_len(),
                    logs=logs,
                )
            except RuntimeError as e:
                search = re.search(
                    r"Based on the available memory, the estimated maximum model length is ([0-9]+)\.",
                    str(e)
                )
                if search is None or attempt == MAX_MODEL_LEN_ESTIMATION_RETRIES:
                    raise
                max_model_len = capped_max_model_len = int(search.group(1))
                self.observer.set_context_length_capped(capped_max_model_len)
                self.observer.update_step(
                    STEP_BENCHMARK,
                    "running",
                    detail=(
                        f"Model does not fit in memory at its full context length. "
                        f"Retrying with --max-model-len = {capped_max_model_len}."
                    ),
                )
                self.stop_vllm_docker()
        raise RuntimeError("vLLM server could not be started.")

    @staticmethod
    def parse_kv_token_size(logs: list[str]) -> int:
        """The KV cache size vLLM reports at startup, in tokens: the budget the deployment splits
        across concurrent sequences."""
        match = re.search(r"GPU KV cache size: ([0-9,]+) tokens", "".join(logs))
        if match is None:
            raise RuntimeError(f"Container failed to start, logs:\n{''.join(logs)}")
        return int(match.group(1).replace(",", ""))

    def prompt_token_values(self, max_model_len: int) -> list[int]:
        """The prompt lengths to benchmark, quadrupling up to what the server accepts. Each server
        boots on its own and may settle on a different context length, so this is recomputed per boot."""
        max_prompt_tokens = max_model_len - self.config.completion_tokens
        n_tokens_values = [1]
        if max_prompt_tokens > 1:
            n_tokens_values.append(min(1024, max_prompt_tokens))
        while n_tokens_values[-1] * 4 < max_prompt_tokens:
            n_tokens_values.append(n_tokens_values[-1] * 4)

        if n_tokens_values[-1] * 2 < max_prompt_tokens:
            n_tokens_values.append(max_prompt_tokens)

        return n_tokens_values

    async def run_sweep(
            self,
            max_num_seqs: int,
            concurrent_request_values: list[int],
            n_tokens_values: list[int],
            server_max_model_len: int = 0,
            progress_base: int = 0,
            progress_total: int = 0,
    ) -> tuple[list[BenchPoint], bool]:
        """Benchmark every point of the grid against the server currently running at max_num_seqs. Returns
        the points measured and whether the user skipped. The sweep only feeds the charts the user reads to
        pick a concurrency, so it can be cut short at any point: whatever points landed so far are kept.

        progress_base is how many points of the whole benchmark (every server) are behind this sweep, so the
        progress bar the frontend draws advances across all of them rather than restarting at each server."""
        bench_points: list[BenchPoint] = []
        point_index = 0

        for n_tokens in n_tokens_values:
            for concurrent_requests in concurrent_request_values:
                self._check_cancelled()
                if self.observer.is_benchmark_skipped():
                    return bench_points, True

                self.observer.update_step(
                    STEP_BENCHMARK,
                    "running",
                    detail=f"Running {concurrent_requests} concurrent request(s) with {n_tokens} prompt token(s) each.",
                )

                try:
                    measured = await self.run_prompt_batch_or_skip(concurrent_requests, n_tokens)
                except (ReadTimeout, APITimeoutError) as exc:
                    # A timeout at this concurrency will only get worse at higher concurrency, so
                    # record the point and stop the sweep rather than grind through the rest.
                    self.observer.set_benchmark_timeout(concurrent_requests)
                    self.observer.update_step(
                        STEP_BENCHMARK,
                        "running",
                        detail=(
                            f"Timed out at {concurrent_requests} concurrent requests (prompt tokens = {n_tokens}) "
                            f"against the --max-num-seqs = {max_num_seqs} server; stopping this series since higher "
                            f"concurrency would time out too. {exc}"
                        ),
                    )
                    break
                except Exception as exc:
                    # The client only sees vLLM's generic 500/"EngineCore encountered an issue". The
                    # container log holds the allocator failure that distinguishes a recoverable OOM.
                    logs = await asyncio.to_thread(container_logs, self.container_name)
                    if is_cuda_out_of_memory(logs):
                        self.last_container_logs = logs or self.last_container_logs
                        raise SweepCudaOutOfMemory(
                            server_max_model_len,
                            bench_points,
                            detail=f"CUDA ran out of memory during the benchmark at --max-num-seqs = {max_num_seqs}.",
                            max_num_seqs=max_num_seqs,
                        ) from exc
                    raise

                if measured is None:
                    return bench_points, True

                point = BenchPoint(
                    max_num_seqs=max_num_seqs,
                    series_id="1 token" if n_tokens == 1 else f"{n_tokens} tokens",
                    concurrent_requests=concurrent_requests,
                    **measured.model_dump(),
                )
                bench_points.append(point)
                self.observer.add_bench_point(point)
                point_index += 1
                self.observer.set_benchmark_progress(progress_base + point_index, progress_total)

        return bench_points, False

    def _halve_max_model_len(self, max_model_len: int) -> int:
        # The completion must still fit, and avoid claiming we made progress once integer division bottoms out.
        halved = max(self.config.completion_tokens + 1, max_model_len // 2)
        if halved >= max_model_len:
            raise RuntimeError("CUDA out of memory and --max-model-len cannot be reduced further.")
        return halved

    @staticmethod
    def _best_working_point(points: list[BenchPoint]) -> BenchPoint:
        # Prefer the point backed by the largest server batch; at that batch prefer the longest prompt,
        # then the highest observed concurrency. Its values seed the recovery form as a useful starting point.
        return max(points,
                   key=lambda point: (point.max_num_seqs, point.median_prompt_tokens, point.concurrent_requests))

    async def _finish_profile(
            self, bench_points: list[BenchPoint], skipped_any: bool, deploy_server: ServerInfo,
            selected: tuple[int, int] | None = None,
    ) -> ProfileResult:
        self._check_cancelled()
        self.observer.update_step(
            STEP_BENCHMARK,
            "done",
            detail="Benchmark complete, some sweeps were skipped." if skipped_any else "Benchmark complete.",
        )
        self.observer.update_step(
            STEP_CONFIGURE,
            "running",
            detail="Choose the --max-num-seqs and --max-model-len values for the Docker command.",
        )
        max_num_seqs, max_model_len = selected or await self.observer.choose_deploy_config(deploy_server)
        docker_command = self.pretty_docker_cmd(build_deploy_cmd(self.config, max_num_seqs, max_model_len))
        self.observer.update_step(
            STEP_CONFIGURE,
            "done",
            detail=f"Using --max-num-seqs = {max_num_seqs}, --max-model-len = {max_model_len}",
        )
        return ProfileResult(
            concurrent_request_values=self.config.concurrent_request_values,
            max_num_seqs_values=self.config.max_num_seqs_values,
            bench_points=bench_points,
            docker_command=docker_command,
            selected_max_num_seqs=max_num_seqs,
            selected_max_model_len=max_model_len,
        )

    async def profile(self) -> ProfileResult:
        # Starts at the user's cap when one was configured; OOM recovery overwrites it with the
        # halved retry length either way.
        requested_max_model_len: int | None = self.config.max_model_len
        automatic_oom_retries = 0

        while True:
            bench_points: list[BenchPoint] = []
            completed_stress_tests: list[StressTestResult] = []
            deploy_server: ServerInfo | None = None
            swept = 0
            try:
                for index, max_num_seqs in enumerate(self.config.max_num_seqs_values):
                    self._check_cancelled()
                    try:
                        server = await self.start_vllm_instance(max_num_seqs, requested_max_model_len)
                        if deploy_server is None:
                            deploy_server = server

                        stress_test = await self.run_full_context_stress(max_num_seqs, server.max_model_len)
                        n_tokens_values = self.prompt_token_values(server.max_model_len)
                        grid_size = len(n_tokens_values) * len(self.config.concurrent_request_values)
                        total = swept + grid_size * (len(self.config.max_num_seqs_values) - index)
                        self.observer.set_benchmark_progress(swept, total)

                        points, skipped = await self.run_sweep(
                            max_num_seqs,
                            self.config.concurrent_request_values,
                            n_tokens_values,
                            server.max_model_len,
                            swept,
                            total,
                        )
                        bench_points.extend(points)
                        swept += grid_size
                        self.observer.set_benchmark_progress(swept, total)
                        if skipped:
                            raise BenchmarkSkipped()
                        completed_stress_tests.append(stress_test)
                        # This server is fully benchmarked, so the points behind us are enough to choose a
                        # deployment from: from here on the user may skip the rest.
                        self.observer.set_benchmark_skippable(True)
                    finally:
                        # Free the GPU before booting the next server — and before handing over to the user.
                        self.stop_vllm_docker()
            except BenchmarkSkipped:
                # Skip means "stop benchmarking, let me choose now" — every remaining server is dropped.
                assert deploy_server is not None
                self.observer.clear_benchmark_skip()
                return await self._finish_profile(bench_points, True, deploy_server)
            except SweepCudaOutOfMemory as oom:
                bench_points.extend(oom.bench_points)
                retry_max_model_len = self._halve_max_model_len(oom.server_max_model_len)
                saved_profile = False
                if bench_points:
                    last_completed_stress_test = completed_stress_tests[-1] if completed_stress_tests else None
                    best = self._best_working_point(bench_points)
                    # Seed recovery with a successful point; the charts remain available for the user to choose
                    # a different interpolation or a proven value from another server batch.
                    options = OomRecoveryOptions(
                        max_num_seqs=last_completed_stress_test.max_num_seqs if last_completed_stress_test else best.max_num_seqs,
                        max_model_len=(
                            last_completed_stress_test.max_model_len
                            if last_completed_stress_test
                            else best.median_prompt_tokens + self.config.completion_tokens
                        ),
                        retry_max_model_len=retry_max_model_len,
                        failure_detail=oom.detail,
                        # A completed stress test means a --max-num-seqs was proven end to end, so its
                        # bench is worth keeping when the user chooses to retry smaller.
                        savable=last_completed_stress_test is not None,
                    )
                    action, max_num_seqs, max_model_len = await self.observer.choose_sweep_oom_recovery(options)
                    if action == "deploy":
                        assert max_num_seqs is not None and max_model_len is not None
                        assert deploy_server is not None
                        return await self._finish_profile(bench_points, False, deploy_server,
                                                          (max_num_seqs, max_model_len))
                    if action == "save_retry":
                        # Persist the proven bench as its own profile before reset_benchmark drops it.
                        assert max_num_seqs is not None and max_model_len is not None
                        docker_command = self.pretty_docker_cmd(
                            build_deploy_cmd(self.config, max_num_seqs, max_model_len)
                        )
                        await asyncio.to_thread(
                            self.observer.save_intermediate_profile,
                            max_num_seqs, max_model_len, docker_command,
                        )
                        saved_profile = True
                    requested_max_model_len = retry_max_model_len
                else:
                    if automatic_oom_retries >= MAX_SWEEP_OOM_MAX_MODEL_LEN_HALVING_RETRIES:
                        raise RuntimeError(
                            "CUDA out of memory before the first benchmark point after "
                            f"{MAX_SWEEP_OOM_MAX_MODEL_LEN_HALVING_RETRIES} --max-model-len retries."
                        ) from oom
                    automatic_oom_retries += 1
                    requested_max_model_len = retry_max_model_len

                self.observer.reset_benchmark()
                saved_note = "Saved the completed benchmark as a profile. " if saved_profile else ""
                message = (
                    f"{oom.detail} {saved_note}Restarting the benchmark with "
                    f"--max-model-len = {requested_max_model_len:,}."
                )
                if oom.reason == "stress_timeout":
                    message += (
                        " To profile with the previous --max-model-len, restart profiling with a higher stress test"
                        " timeout."
                    )
                self.observer.set_context_length_capped(
                    requested_max_model_len, oom.reason, oom.max_num_seqs, message
                )
                self.observer.update_step(STEP_BENCHMARK, "running", detail=message)
                continue
            else:
                break

        if deploy_server is None:
            raise RuntimeError("vLLM server could not be started.")
        return await self._finish_profile(bench_points, False, deploy_server)

    async def run(self) -> ProfileResult:
        try:
            await self.download_model()
            return await self.profile()
        finally:
            self.stop_vllm_docker()
            await self.bench.aclose()
