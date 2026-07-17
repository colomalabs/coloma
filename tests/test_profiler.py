import asyncio
import json

import pytest
from fastapi import HTTPException

from backend import profiler
from backend.llm_profiler import OomRecoveryOptions, ProfilerConfig, ServerInfo


# A snapshot as written by an older build, whose bench points carried low/high pairs
# instead of today's series_id/prompt_tokens/ttft/throughput.
LEGACY_SNAPSHOT = {
    "id": "af3ce1c44974",
    "status": "running",
    "created_at": "2026-07-10T18:17:53+00:00",
    "updated_at": "2026-07-10T18:17:53+00:00",
    "config": {"model_name": "test-model"},
    "steps": [],
    "bench_points": [
        {
            "num_seqs": 1,
            "prompt_tokens_low": 1,
            "ttft_low": 0.02,
            "throughput_low": 207.8,
            "prompt_tokens_high": 2048,
            "ttft_high": 0.3,
            "throughput_high": 190.1,
        }
    ],
}


def store_snapshot(tmp_path, monkeypatch, snapshot: dict, active: int = 1) -> None:
    monkeypatch.setattr(profiler, "PROFILER_DB_PATH", tmp_path / "profiler.sqlite3")
    with profiler.db_connection() as conn:
        conn.execute(
            """
            INSERT INTO profiler_jobs (id, status, created_at, updated_at, active, snapshot_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot["id"],
                snapshot["status"],
                snapshot["created_at"],
                snapshot["updated_at"],
                active,
                json.dumps(snapshot),
            ),
        )
        conn.commit()


def test_active_job_snapshot_ignores_a_snapshot_from_an_older_schema(tmp_path, monkeypatch):
    store_snapshot(tmp_path, monkeypatch, LEGACY_SNAPSHOT)

    assert profiler.load_active_job_snapshot() is None


def test_unreadable_active_job_is_cleared_so_it_stops_blocking_deploys(tmp_path, monkeypatch):
    store_snapshot(tmp_path, monkeypatch, LEGACY_SNAPSHOT)

    profiler.load_active_job_snapshot()

    with profiler.db_connection() as conn:
        row = conn.execute("SELECT active FROM profiler_jobs WHERE id = ?", (LEGACY_SNAPSHOT["id"],)).fetchone()
    # The row is kept for the record, but no longer reported as the running job.
    assert row["active"] == 0


def test_job_snapshot_by_id_ignores_a_snapshot_from_an_older_schema(tmp_path, monkeypatch):
    store_snapshot(tmp_path, monkeypatch, LEGACY_SNAPSHOT, active=0)

    assert profiler.load_job_snapshot(LEGACY_SNAPSHOT["id"]) is None


def test_a_job_left_running_by_a_previous_process_is_cancelled_at_startup(tmp_path, monkeypatch):
    # A snapshot of the current schema, so it is readable — the job is simply orphaned.
    job = profiler.ProfilerJob(ProfilerConfig(model_name="test-model"))
    job.status = "running"
    snapshot = json.loads(job.snapshot().model_dump_json())
    store_snapshot(tmp_path, monkeypatch, snapshot)
    monkeypatch.setattr(profiler, "stop_named_containers", lambda job_id: None)

    profiler.reap_orphaned_jobs()

    # Nothing is left to drive it, so it must not keep blocking deploys or offering Skip/Submit.
    assert profiler.load_active_job_snapshot() is None
    reaped = profiler.load_job_snapshot(job.id)
    assert reaped.status == "cancelled"
    assert "restart" in reaped.error


def test_a_terminal_job_is_not_cancelled_or_left_active_at_startup(tmp_path, monkeypatch):
    job = profiler.ProfilerJob(ProfilerConfig(model_name="test-model"))
    job.status = "done"
    store_snapshot(tmp_path, monkeypatch, json.loads(job.snapshot().model_dump_json()))

    profiler.reap_orphaned_jobs()

    with profiler.db_connection() as conn:
        row = conn.execute("SELECT status, active FROM profiler_jobs WHERE id = ?", (job.id,)).fetchone()
    assert row["status"] == "done"
    assert row["active"] == 0


def test_job_snapshot_reports_its_configured_server_batch_sizes():
    job = profiler.ProfilerJob(
        ProfilerConfig(model_name="test-model", max_num_seqs_values=[32, 8])
    )

    snapshot = job.snapshot()

    assert snapshot.config.max_num_seqs_values == [8, 32]
    assert snapshot.benchmarked_max_num_seqs_values == [8, 32]


def test_profiler_defaults_endpoint_uses_profiler_config_defaults():
    defaults = asyncio.run(profiler.profiler_defaults())
    config = ProfilerConfig(model_name="test-model")

    assert defaults.max_num_seqs_values == config.max_num_seqs_values
    assert defaults.concurrent_request_values == config.concurrent_request_values


def test_context_length_warnings_survive_benchmark_restarts(tmp_path, monkeypatch):
    monkeypatch.setattr(profiler, "PROFILER_DB_PATH", tmp_path / "profiler.sqlite3")
    job = profiler.ProfilerJob(ProfilerConfig(model_name="test-model"))

    job.set_context_length_capped(131072, "oom", 32, "Ran out of memory at --max-num-seqs = 32.")
    job.reset_benchmark()
    job.set_context_length_capped(65536, "stress_timeout", 16, "Stress test timed out at --max-num-seqs = 16.")

    assert [warning.model_dump() for warning in job.snapshot().context_length_warnings] == [
        {
            "max_model_len": 131072,
            "reason": "oom",
            "max_num_seqs": 32,
            "message": "Ran out of memory at --max-num-seqs = 32.",
        },
        {
            "max_model_len": 65536,
            "reason": "stress_timeout",
            "max_num_seqs": 16,
            "message": "Stress test timed out at --max-num-seqs = 16.",
        },
    ]


def test_artifacts_saved_before_warnings_carried_a_message_get_one_back():
    # Older builds stored only the last cap, and no wording for it.
    restored = profiler.restore_context_length_warnings(
        {"context_length_capped": 65536, "context_length_capped_reason": "stress_timeout"}
    )

    assert restored["context_length_warnings"] == [
        {
            "max_model_len": 65536,
            "reason": "stress_timeout",
            "max_num_seqs": None,
            "message": "Full-context stress test timed out. Restarted the benchmark with --max-model-len = 65,536.",
        }
    ]


def test_skipping_is_refused_until_a_server_has_been_benchmarked(tmp_path, monkeypatch):
    monkeypatch.setattr(profiler, "PROFILER_DB_PATH", tmp_path / "profiler.sqlite3")
    job = profiler.ProfilerJob(ProfilerConfig(model_name="test-model"))
    job.status = "running"
    profiler.active_jobs[job.id] = job

    try:
        # A skip now drops every remaining server and jumps to the deploy choice, so before the first
        # server is benchmarked there would be nothing to choose from.
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(profiler.skip_profiler_benchmark(job.id))
        assert exc_info.value.status_code == 409

        job.set_benchmark_skippable(True)
        snapshot = asyncio.run(profiler.skip_profiler_benchmark(job.id))
    finally:
        profiler.active_jobs.pop(job.id, None)

    assert snapshot.benchmark_skipped is True
    assert job.benchmark_skip_event.is_set()


def test_reaping_removes_the_container_the_orphaned_job_left_behind(tmp_path, monkeypatch):
    job = profiler.ProfilerJob(ProfilerConfig(model_name="test-model"))
    job.status = "running"
    store_snapshot(tmp_path, monkeypatch, json.loads(job.snapshot().model_dump_json()))
    stopped = []
    monkeypatch.setattr(profiler, "stop_named_containers", stopped.append)

    profiler.reap_orphaned_jobs()

    # Otherwise it keeps holding the GPU and port 8000 against the next job.
    assert stopped == [job.id]


def test_terminal_job_is_served_once_then_removed_from_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(profiler, "PROFILER_DB_PATH", tmp_path / "profiler.sqlite3")
    job = profiler.ProfilerJob(ProfilerConfig(model_name="test-model"))
    job.status = "done"
    profiler.active_jobs[job.id] = job
    monkeypatch.setattr(profiler, "active_job_id", job.id)

    try:
        first = asyncio.run(profiler.active_profiler_job())
    finally:
        profiler.active_jobs.pop(job.id, None)
        profiler.active_job_id = None

    assert first is not None and first.status == "done"
    assert job.id not in profiler.active_jobs
    assert profiler.active_job_id is None


SERVER = ServerInfo(kv_token_size=100_000, max_model_len=32_768, logs=[])


def waiting_job(tmp_path, monkeypatch) -> profiler.ProfilerJob:
    """A job parked on the deploy choice, as it is once the benchmark is done."""
    monkeypatch.setattr(profiler, "PROFILER_DB_PATH", tmp_path / "profiler.sqlite3")
    job = profiler.ProfilerJob(ProfilerConfig(model_name="test-model"))
    job.status = "running"
    profiler.active_jobs[job.id] = job
    return job


async def choose(job, **selection):
    """Answer the choice the job is blocked on, and return what choose_deploy_config unblocks with."""
    waiter = asyncio.ensure_future(job.choose_deploy_config(SERVER))
    await asyncio.sleep(0)  # let the waiter register awaiting_deploy_config
    await profiler.choose_profiler_deploy_config(job.id, profiler.DeploySelection(**selection))
    return await waiter


def test_the_deploy_choice_returns_both_values_as_picked(tmp_path, monkeypatch):
    job = waiting_job(tmp_path, monkeypatch)
    try:
        max_num_seqs, max_model_len = asyncio.run(choose(job, max_num_seqs=16, max_model_len=8192))
    finally:
        profiler.active_jobs.pop(job.id, None)

    # No longer derived from the KV budget: what the user typed is what gets deployed.
    assert (max_num_seqs, max_model_len) == (16, 8192)
    assert job.snapshot().selected_max_num_seqs == 16
    assert job.snapshot().selected_max_model_len == 8192


@pytest.mark.parametrize(
    ("max_num_seqs", "max_model_len"),
    [(-7, 0), (1, SERVER.max_model_len + 1)],
)
def test_the_deploy_choice_accepts_user_values_without_bounds(
        tmp_path, monkeypatch, max_num_seqs, max_model_len
):
    job = waiting_job(tmp_path, monkeypatch)
    try:
        selected = asyncio.run(
            choose(job, max_num_seqs=max_num_seqs, max_model_len=max_model_len)
        )
    finally:
        profiler.active_jobs.pop(job.id, None)

    assert selected == (max_num_seqs, max_model_len)


def test_oom_recovery_deploy_choice_accepts_user_values(tmp_path, monkeypatch):
    job = waiting_job(tmp_path, monkeypatch)
    job.awaiting_oom_recovery = True
    job.oom_recovery = OomRecoveryOptions(max_num_seqs=4, max_model_len=1034, retry_max_model_len=4096)
    try:
        snapshot = asyncio.run(
            profiler.choose_profiler_oom_recovery(
                job.id,
                profiler.OomRecoverySelection(action="deploy", max_num_seqs=-31, max_model_len=0),
            )
        )
    finally:
        profiler.active_jobs.pop(job.id, None)

    assert snapshot.awaiting_oom_recovery is False
    assert snapshot.oom_recovery is None
    assert (snapshot.selected_max_num_seqs, snapshot.selected_max_model_len) == (-31, 0)


def test_oom_recovery_retry_wakes_the_waiting_profiler(tmp_path, monkeypatch):
    job = waiting_job(tmp_path, monkeypatch)
    options = OomRecoveryOptions(max_num_seqs=4, max_model_len=1034, retry_max_model_len=4096)

    async def scenario():
        waiter = asyncio.ensure_future(job.choose_sweep_oom_recovery(options))
        await asyncio.sleep(0)
        await profiler.choose_profiler_oom_recovery(job.id, profiler.OomRecoverySelection(action="retry"))
        return await waiter

    try:
        result = asyncio.run(scenario())
    finally:
        profiler.active_jobs.pop(job.id, None)

    assert result == ("retry", None, None)


def test_oom_recovery_save_retry_carries_values_without_choosing_config(tmp_path, monkeypatch):
    job = waiting_job(tmp_path, monkeypatch)
    options = OomRecoveryOptions(max_num_seqs=4, max_model_len=8192, retry_max_model_len=4096, savable=True)

    async def scenario():
        waiter = asyncio.ensure_future(job.choose_sweep_oom_recovery(options))
        await asyncio.sleep(0)
        snapshot = await profiler.choose_profiler_oom_recovery(
            job.id,
            profiler.OomRecoverySelection(action="save_retry", max_num_seqs=4, max_model_len=8192),
        )
        return snapshot, await waiter

    try:
        snapshot, result = asyncio.run(scenario())
    finally:
        profiler.active_jobs.pop(job.id, None)

    assert result == ("save_retry", 4, 8192)
    # save_retry must not look like a finished deploy choice, or the frontend would jump to the deploy step.
    assert (snapshot.selected_max_num_seqs, snapshot.selected_max_model_len) == (None, None)
    assert snapshot.awaiting_oom_recovery is False


def test_a_failing_job_reports_the_container_logs_under_the_traceback(monkeypatch):
    # vLLM's own stack trace only exists inside the container: the API error our traceback ends on
    # ("EngineCore encountered an issue. See stack trace (above)") never says what actually broke.
    monkeypatch.setattr(profiler, "container_logs", lambda name: "ValueError: max_num_batched_tokens")

    report, root_cause = profiler.failure_report("some-container", "Traceback (most recent call last):\n  openai.APIError\n")

    assert "openai.APIError" in report
    assert "ValueError: max_num_batched_tokens" in report
    assert root_cause == "ValueError: max_num_batched_tokens"


def test_a_failing_job_reports_the_traceback_alone_when_no_container_ran(monkeypatch):
    # Nothing to read from a job that died before its first server booted.
    monkeypatch.setattr(profiler, "container_logs", lambda name: "")

    report, root_cause = profiler.failure_report("some-container", "Traceback (most recent call last):\n  RuntimeError\n")

    assert report == "Traceback (most recent call last):\n  RuntimeError\n"
    assert "vLLM container logs" not in report
    assert root_cause == ""


def test_the_failure_report_falls_back_to_the_logs_kept_at_teardown(monkeypatch):
    # What actually happens: a failing sweep unwinds through profile()'s teardown, which removes the
    # container, so by the time the job reports there is nothing left to read off it.
    monkeypatch.setattr(profiler, "container_logs", lambda name: "")

    report, _ = profiler.failure_report(
        "gone-container",
        "Traceback:\n  openai.APIError\n",
        fallback_logs="ValueError: max_num_batched_tokens",
    )

    assert "openai.APIError" in report
    assert "ValueError: max_num_batched_tokens" in report
