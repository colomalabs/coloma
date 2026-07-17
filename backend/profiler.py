import asyncio
import contextlib
import datetime as dt
import json
import sqlite3
import subprocess
import threading
import traceback
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError

from backend.logger import logger
from backend.config import read_app_config
from backend.tasks import spawn_background_task
from backend.llm_profiler import (
    CONTAINER_PREFIX,
    CONCURRENT_REQUEST_VALUES,
    MAX_NUM_SEQS_VALUES,
    PROFILER_HF_HOME,
    PROFILER_VLLM_HOME,
    STEP_BENCHMARK,
    STEP_CONFIGURE,
    STEP_TITLES,
    BenchPoint,
    LlmProfiler,
    OomRecoveryOptions,
    ProfilerConfig,
    ServerInfo,
    StressTestResult,
    container_logs,
    container_root_cause,
    stop_named_containers,
    validate_config,
)


PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data"
PROFILER_DB_PATH = DATA_DIR / "profiler_artifacts.sqlite3"


class ProfilerStep(BaseModel):
    id: int
    title: str
    status: Literal["pending", "running", "done", "error", "skipped", "cancelled"] = "pending"
    detail: str = ""
    result: dict[str, Any] = Field(default_factory=dict)
    logs: list[str] = Field(default_factory=list)
    error: str = ""


class ContextLengthWarning(BaseModel):
    max_model_len: int
    reason: Literal["oom", "stress_timeout"]
    max_num_seqs: int | None = None
    # The sentence the frontend shows. The backend words every failure it reports, so the UI never has
    # to reconstruct what went wrong from the structured fields.
    message: str = ""


def legacy_context_length_warning_message(max_model_len: int, reason: Literal["oom", "stress_timeout"]) -> str:
    """Word a warning stored before the backend started sending its own sentence, so old artifacts still
    explain themselves. Live runs pass the failure's own wording instead."""
    cause = (
        "Full-context stress test timed out"
        if reason == "stress_timeout"
        else "Not enough memory to fit the whole context length"
    )
    return f"{cause}. Restarted the benchmark with --max-model-len = {max_model_len:,}."


class ProfilerJobSnapshot(BaseModel):
    id: str
    status: Literal["queued", "running", "done", "error", "cancelled"]
    created_at: str
    updated_at: str
    config: ProfilerConfig
    steps: list[ProfilerStep]
    bench_points: list[BenchPoint] = Field(default_factory=list)
    stress_tests: list[StressTestResult] = Field(default_factory=list)
    benchmark_timeout_num_seqs: int | None = None
    benchmark_skipped: bool = False
    # False until a server has been benchmarked end to end: skipping before that leaves nothing to deploy from.
    benchmark_skippable: bool = False
    # Sweep points measured out of the total planned across every server, for the progress bar. The total
    # is 0 until the first server boots, since the grid depends on the context length it settles on.
    benchmark_progress_done: int = 0
    benchmark_progress_total: int = 0
    context_length_capped: int | None = None
    context_length_capped_reason: Literal["oom", "stress_timeout"] | None = None
    context_length_warnings: list[ContextLengthWarning] = Field(default_factory=list)
    # Set only while a sweep OOM has some usable points and is waiting for the user's decision.
    awaiting_oom_recovery: bool = False
    oom_recovery: OomRecoveryOptions | None = None
    # What the profiled server booted with: the KV cache budget, and the context length it settled
    # on. They bound and seed the deploy choice below.
    kv_token_size: int | None = None
    server_max_model_len: int | None = None
    # The --max-num-seqs values the servers were benchmarked at. The UI labels one group of charts per
    # value, and offers the largest as the default --max-num-seqs.
    benchmarked_max_num_seqs_values: list[int] = Field(default_factory=lambda: list(MAX_NUM_SEQS_VALUES))
    # True only once the benchmark is done and the job is blocked waiting for the deploy choice.
    awaiting_deploy_config: bool = False
    selected_max_num_seqs: int | None = None
    selected_max_model_len: int | None = None
    docker_command: str = ""
    artifact_id: int | None = None
    error: str = ""


class ProfilerDefaults(BaseModel):
    max_num_seqs_values: list[int] = Field(default_factory=lambda: list(MAX_NUM_SEQS_VALUES))
    concurrent_request_values: list[int] = Field(default_factory=lambda: list(CONCURRENT_REQUEST_VALUES))


class ProfilerArtifactSummary(BaseModel):
    id: int
    model_name: str
    vllm_version: str
    created_at: str
    docker_command: str


class ProfilerArtifact(BaseModel):
    id: int
    model_name: str
    vllm_version: str
    config: dict[str, Any]
    created_at: str
    profiling_results: dict[str, Any]
    docker_command: str


class DeploySelection(BaseModel):
    max_num_seqs: int
    max_model_len: int


class OomRecoverySelection(BaseModel):
    action: Literal["deploy", "retry", "save_retry"]
    max_num_seqs: int | None = None
    max_model_len: int | None = None


class ProfilerJob:
    def __init__(self, config: ProfilerConfig):
        now = dt.datetime.now(dt.UTC).isoformat()
        self.id = uuid.uuid4().hex[:12]
        self.config = config
        self.status: Literal["queued", "running", "done", "error", "cancelled"] = "queued"
        self.created_at = now
        self.updated_at = now
        self.steps = [ProfilerStep(id=index + 1, title=title) for index, title in enumerate(STEP_TITLES)]
        self.bench_points: list[BenchPoint] = []
        self.stress_tests: list[StressTestResult] = []
        self.benchmark_timeout_num_seqs: int | None = None
        self.benchmark_skipped = False
        self.benchmark_skippable = False
        self.benchmark_progress_done = 0
        self.benchmark_progress_total = 0
        self.benchmark_skip_event = asyncio.Event()
        self.context_length_capped: int | None = None
        self.context_length_capped_reason: Literal["oom", "stress_timeout"] | None = None
        self.context_length_warnings: list[ContextLengthWarning] = []
        self.awaiting_oom_recovery = False
        self.oom_recovery: OomRecoveryOptions | None = None
        self.oom_recovery_action: Literal["deploy", "retry", "save_retry"] | None = None
        # The values to save the completed bench with on a save-and-retry, carried from the endpoint to
        # the sweep without touching selected_*, which would make the frontend think config was chosen.
        self.oom_recovery_values: tuple[int, int] | None = None
        self.oom_recovery_event = asyncio.Event()
        self.kv_token_size: int | None = None
        self.server_max_model_len: int | None = None
        self.awaiting_deploy_config = False
        self.deploy_config_event = asyncio.Event()
        self.selected_max_num_seqs: int | None = None
        self.selected_max_model_len: int | None = None
        self.docker_command = ""
        self.artifact_id: int | None = None
        self.error = ""
        self.cancel_requested = False
        self.active_process: subprocess.Popen[str] | None = None
        self.container_name = f"{CONTAINER_PREFIX}-{self.id}"
        self.lock = threading.Lock()

    def is_cancelled(self) -> bool:
        with self.lock:
            return self.cancel_requested

    def request_cancel(self) -> subprocess.Popen[str] | None:
        """Flag the job as cancelled and return the process to terminate, if any.

        Also wakes any pending user-selection waits so they can observe the flag.
        """
        with self.lock:
            self.cancel_requested = True
            proc = self.active_process
        self.deploy_config_event.set()
        self.oom_recovery_event.set()
        return proc

    def is_benchmark_skipped(self) -> bool:
        with self.lock:
            return self.benchmark_skipped

    async def wait_benchmark_skipped(self) -> None:
        await self.benchmark_skip_event.wait()

    def request_skip_benchmark(self) -> None:
        with self.lock:
            self.benchmark_skipped = True
            self.touch()
        self.benchmark_skip_event.set()
        persist_job_snapshot(self)

    def clear_benchmark_skip(self) -> None:
        """Called once the profiler has acted on the skip and is on its way to the deploy choice."""
        with self.lock:
            self.benchmark_skipped = False
            self.touch()
        self.benchmark_skip_event.clear()
        persist_job_snapshot(self)

    def set_benchmark_skippable(self, skippable: bool) -> None:
        """A skip drops every server still to be benchmarked and jumps to the deploy choice, so it is only
        offered once one server has been benchmarked end to end — before that there is nothing to choose from."""
        with self.lock:
            if self.benchmark_skippable == skippable:
                return
            self.benchmark_skippable = skippable
            self.touch()
        persist_job_snapshot(self)

    def set_process(self, proc: subprocess.Popen[str] | None) -> None:
        with self.lock:
            self.active_process = proc

    def snapshot(self) -> ProfilerJobSnapshot:
        with self.lock:
            return ProfilerJobSnapshot(
                id=self.id,
                status=self.status,
                created_at=self.created_at,
                updated_at=self.updated_at,
                config=ProfilerConfig.model_validate(self.config.model_dump()),
                steps=[step.model_copy(deep=True) for step in self.steps],
                bench_points=[point.model_copy(deep=True) for point in self.bench_points],
                stress_tests=[result.model_copy(deep=True) for result in self.stress_tests],
                benchmark_timeout_num_seqs=self.benchmark_timeout_num_seqs,
                benchmark_skipped=self.benchmark_skipped,
                benchmark_skippable=self.benchmark_skippable,
                benchmark_progress_done=self.benchmark_progress_done,
                benchmark_progress_total=self.benchmark_progress_total,
                context_length_capped=self.context_length_capped,
                context_length_capped_reason=self.context_length_capped_reason,
                context_length_warnings=[warning.model_copy() for warning in self.context_length_warnings],
                awaiting_oom_recovery=self.awaiting_oom_recovery,
                oom_recovery=self.oom_recovery.model_copy() if self.oom_recovery is not None else None,
                kv_token_size=self.kv_token_size,
                server_max_model_len=self.server_max_model_len,
                benchmarked_max_num_seqs_values=list(self.config.max_num_seqs_values),
                awaiting_deploy_config=self.awaiting_deploy_config,
                selected_max_num_seqs=self.selected_max_num_seqs,
                selected_max_model_len=self.selected_max_model_len,
                docker_command=self.docker_command,
                artifact_id=self.artifact_id,
                error=self.error,
            )

    def touch(self) -> None:
        self.updated_at = dt.datetime.now(dt.UTC).isoformat()

    def set_status(self, status: Literal["queued", "running", "done", "error", "cancelled"], error: str = "") -> None:
        with self.lock:
            self.status = status
            self.error = error
            self.touch()
        persist_job_snapshot(self)

    def update_step(
        self,
        step_id: int,
        status: Literal["pending", "running", "done", "error", "skipped", "cancelled"] | None = None,
        detail: str | None = None,
        result: dict[str, Any] | None = None,
        logs: list[str] | None = None,
        error: str | None = None,
    ) -> None:
        with self.lock:
            step = self.steps[step_id - 1]
            if status is not None:
                step.status = status
            if detail is not None:
                step.detail = detail
            if result is not None:
                step.result = result
            if logs is not None:
                step.logs = logs[-80:]
            if error is not None:
                step.error = error
            self.touch()
        persist_job_snapshot(self)

    def add_bench_point(self, point: BenchPoint) -> None:
        with self.lock:
            self.bench_points.append(point)
            self.touch()
        persist_job_snapshot(self)

    def add_stress_test(self, result: StressTestResult) -> None:
        with self.lock:
            self.stress_tests.append(result)
            self.touch()
        persist_job_snapshot(self)

    def set_benchmark_progress(self, done: int, total: int) -> None:
        with self.lock:
            self.benchmark_progress_done = done
            self.benchmark_progress_total = total
            self.touch()
        persist_job_snapshot(self)

    def set_benchmark_timeout(self, num_seqs: int) -> None:
        with self.lock:
            self.benchmark_timeout_num_seqs = num_seqs
            self.touch()
        persist_job_snapshot(self)

    def set_context_length_capped(
            self,
            capped_max_model_len: int,
            reason: Literal["oom", "stress_timeout"] = "oom",
            max_num_seqs: int | None = None,
            message: str = "",
    ) -> None:
        with self.lock:
            self.context_length_capped = capped_max_model_len
            self.context_length_capped_reason = reason
            self.context_length_warnings.append(
                ContextLengthWarning(
                    max_model_len=capped_max_model_len,
                    reason=reason,
                    max_num_seqs=max_num_seqs,
                    message=message or legacy_context_length_warning_message(capped_max_model_len, reason),
                )
            )
            self.touch()
        persist_job_snapshot(self)

    def reset_benchmark(self) -> None:
        """Discard an incomplete run before restarting it at a smaller context length.

        Mixing points from two --max-model-len values would make the charts and recovery limits lie.
        """
        with self.lock:
            self.bench_points = []
            self.stress_tests = []
            self.benchmark_timeout_num_seqs = None
            self.benchmark_skipped = False
            # The restart throws away the points a skip would have deployed from, so it also re-arms the gate.
            self.benchmark_skippable = False
            self.benchmark_progress_done = 0
            self.benchmark_progress_total = 0
            self.touch()
        self.benchmark_skip_event.clear()
        persist_job_snapshot(self)

    def save_intermediate_profile(
            self, selected_max_num_seqs: int, selected_max_model_len: int, docker_command: str
    ) -> int:
        """Persist the completed benchmark as its own profile before a save-and-retry restart discards it.

        The retry runs a fresh sweep and makes its own deploy choice, so selected_* and docker_command are
        set only long enough for save_artifact to read them, then cleared — the frontend keys "configuration
        chosen" off selected_max_num_seqs."""
        with self.lock:
            self.selected_max_num_seqs = selected_max_num_seqs
            self.selected_max_model_len = selected_max_model_len
            self.docker_command = docker_command
            self.touch()
        artifact_id = save_artifact(self)
        with self.lock:
            self.selected_max_num_seqs = None
            self.selected_max_model_len = None
            self.docker_command = ""
            self.touch()
        persist_job_snapshot(self)
        return artifact_id

    async def choose_sweep_oom_recovery(
            self, options: OomRecoveryOptions
    ) -> tuple[Literal["deploy", "retry", "save_retry"], int | None, int | None]:
        """Pause a partly successful sweep so the frontend can deploy its proven limits or retry smaller."""
        with self.lock:
            self.oom_recovery = options.model_copy()
            self.oom_recovery_action = None
            self.awaiting_oom_recovery = True
            self.touch()
        self.oom_recovery_event.clear()
        persist_job_snapshot(self)
        self.update_step(
            STEP_BENCHMARK,
            "running",
            detail=options.failure_detail,
        )
        while True:
            await self.oom_recovery_event.wait()
            if self.is_cancelled():
                raise asyncio.CancelledError()
            with self.lock:
                action = self.oom_recovery_action
                if action == "deploy":
                    return action, self.selected_max_num_seqs, self.selected_max_model_len
                if action == "save_retry":
                    values = self.oom_recovery_values
                    self.oom_recovery_values = None
                    assert values is not None
                    return action, values[0], values[1]
                if action == "retry":
                    return action, None, None
            self.oom_recovery_event.clear()

    async def choose_deploy_config(self, server: ServerInfo) -> tuple[int, int]:
        """Terminal choice: block until the user picks --max-num-seqs and --max-model-len in the
        frontend, then return them. Publishing the server's KV budget and context length lets the UI
        seed the defaults and show how many full-context requests fit in the cache."""
        with self.lock:
            self.kv_token_size = server.kv_token_size
            self.server_max_model_len = server.max_model_len
            self.selected_max_num_seqs = None
            self.selected_max_model_len = None
            self.awaiting_deploy_config = True
            self.touch()
        persist_job_snapshot(self)
        self.update_step(STEP_CONFIGURE, "running", detail="Choose the --max-num-seqs and --max-model-len for the deployment.")
        # The event is set by the choose-deploy endpoint (after storing the
        # selection) or by request_cancel, so no polling is needed.
        while True:
            await self.deploy_config_event.wait()
            if self.is_cancelled():
                raise asyncio.CancelledError()
            with self.lock:
                if self.selected_max_num_seqs is not None and self.selected_max_model_len is not None:
                    return self.selected_max_num_seqs, self.selected_max_model_len
            self.deploy_config_event.clear()


def failure_report(container_name: str, error_traceback: str, fallback_logs: str = "") -> tuple[str, str]:
    """What a failed job shows: the full report for the failing step, and the one-line summary for the job.

    The container's logs are read off it if it is somehow still up, and otherwise come from the copy taken
    when it was torn down (there are none at all for a job that died before its first server booted). The
    exception they end on is the real cause — a CUDA OOM, say — so it leads both the report and the
    summary, rather than our own traceback's "EngineCore encountered an issue. See stack trace (above)".
    """
    logs = container_logs(container_name) or fallback_logs
    if not logs:
        return error_traceback, ""
    root_cause = container_root_cause(logs)
    header = f"{root_cause}\n\n" if root_cause else ""
    return f"{header}{error_traceback}\n--- vLLM container logs ---\n{logs}", root_cause


def exception_debug_message(exc: BaseException) -> str:
    message = str(exc).strip()
    if message:
        return message
    return f"{type(exc).__module__}.{type(exc).__name__}: {exc!r}"


router = APIRouter()
active_jobs: dict[str, ProfilerJob] = {}
# Only read/written from the event loop thread; no lock needed.
active_job_id: str | None = None


def ensure_profiler_dirs() -> None:
    Path(PROFILER_HF_HOME).expanduser().mkdir(parents=True, exist_ok=True)
    Path(PROFILER_VLLM_HOME).expanduser().mkdir(parents=True, exist_ok=True)


_initialized_db_paths: set[str] = set()


def db_connection(db_path: Path | None = None) -> sqlite3.Connection:
    db_path = PROFILER_DB_PATH if db_path is None else db_path
    ensure_profiler_dirs()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # Run the schema DDL once per db path instead of on every connection.
    if str(db_path) not in _initialized_db_paths:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profiler_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_name TEXT NOT NULL,
                vllm_version TEXT NOT NULL,
                config_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                profiling_results_json TEXT NOT NULL,
                docker_command TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profiler_jobs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 0,
                snapshot_json TEXT NOT NULL
            )
            """
        )
        conn.commit()
        _initialized_db_paths.add(str(db_path))
    return conn


# Snapshot persistence runs on a single worker thread: it keeps blocking sqlite
# writes off the event loop while preserving write order.
_persist_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="profiler-persist")


def _log_persist_failure(future: Future) -> None:
    exc = future.exception()
    if exc is not None:
        logger.error("Could not persist profiler job snapshot: %s", exc)


def _write_job_snapshot(snapshot: ProfilerJobSnapshot, is_active: bool, db_path: Path) -> None:
    with db_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO profiler_jobs (id, status, created_at, updated_at, active, snapshot_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status = excluded.status,
                updated_at = excluded.updated_at,
                active = excluded.active,
                snapshot_json = excluded.snapshot_json
            """,
            (snapshot.id, snapshot.status, snapshot.created_at, snapshot.updated_at, 1 if is_active else 0, snapshot.model_dump_json()),
        )
        if is_active:
            conn.execute("UPDATE profiler_jobs SET active = 0 WHERE id != ?", (snapshot.id,))
        conn.commit()


def persist_job_snapshot(job: ProfilerJob, active: bool | None = None) -> None:
    snapshot = job.snapshot()
    is_active = snapshot.status in {"queued", "running"} if active is None else active
    # Capture the db path now: the executor may run after a test has swapped it.
    future = _persist_executor.submit(_write_job_snapshot, snapshot, is_active, PROFILER_DB_PATH)
    future.add_done_callback(_log_persist_failure)


def parse_job_snapshot(job_id: str, snapshot_json: str) -> ProfilerJobSnapshot | None:
    """Stored snapshots outlive the code that wrote them, so one written against an older
    schema no longer validates. Reporting no job beats raising out of every endpoint that
    asks whether the profiler is busy."""
    try:
        return ProfilerJobSnapshot.model_validate_json(snapshot_json)
    except ValidationError as exc:
        logger.warning(
            "Ignoring profiler job %s: its stored snapshot does not match the current schema (%d errors)",
            job_id,
            exc.error_count(),
        )
        return None


def load_job_snapshot(job_id: str) -> ProfilerJobSnapshot | None:
    with db_connection() as conn:
        row = conn.execute("SELECT snapshot_json FROM profiler_jobs WHERE id = ?", (job_id,)).fetchone()
    return None if row is None else parse_job_snapshot(job_id, row["snapshot_json"])


def load_active_job_snapshot() -> ProfilerJobSnapshot | None:
    with db_connection() as conn:
        row = conn.execute(
            "SELECT id, snapshot_json FROM profiler_jobs WHERE active = 1 ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        snapshot = parse_job_snapshot(row["id"], row["snapshot_json"])
        if snapshot is None:
            # A snapshot this process cannot read was written by an older build, so no job is
            # running behind it. Clear the flag: while it stays active it blocks every deploy
            # and pressure test with "Profiler is running".
            conn.execute("UPDATE profiler_jobs SET active = 0 WHERE id = ?", (row["id"],))
            conn.commit()
        return snapshot


def reap_orphaned_jobs() -> None:
    """A profiler job cannot outlive the process that ran it: its asyncio state is gone, and nothing
    is left to drive the container it was benchmarking. The database still records it as live, so the
    UI keeps offering Skip and Submit on a job no endpoint can find, and the active flag blocks every
    deploy and pressure test. Lay those jobs to rest at startup and remove the containers they left
    holding the GPU."""
    with db_connection() as conn:
        rows = conn.execute(
            "SELECT id, snapshot_json FROM profiler_jobs WHERE status IN ('queued', 'running')"
        ).fetchall()
        for row in rows:
            snapshot = parse_job_snapshot(row["id"], row["snapshot_json"])
            # An unreadable snapshot still gets its columns cleared; there is just no JSON to rewrite.
            snapshot_json = (
                row["snapshot_json"]
                if snapshot is None
                else snapshot.model_copy(
                    update={"status": "cancelled", "error": "Interrupted by a backend restart"}
                ).model_dump_json()
            )
            conn.execute(
                "UPDATE profiler_jobs SET status = 'cancelled', active = 0, snapshot_json = ? WHERE id = ?",
                (snapshot_json, row["id"]),
            )
        # Older builds marked terminal snapshots active so the frontend could fetch their final state.
        # They are no longer live jobs and must not keep the active flag blocking other work.
        conn.execute("UPDATE profiler_jobs SET active = 0 WHERE status NOT IN ('queued', 'running')")
        conn.commit()
    for row in rows:
        stop_named_containers(row["id"])
    if rows:
        logger.warning("Cancelled %d profiler job(s) left running by a previous backend process", len(rows))


def save_artifact(job: ProfilerJob) -> int:
    snapshot = job.snapshot()
    profiling_results = {
        "steps": {str(step.id): step.model_dump() for step in snapshot.steps},
        "bench_points": [point.model_dump() for point in snapshot.bench_points],
        "stress_tests": [result.model_dump() for result in snapshot.stress_tests],
        "benchmark_timeout_num_seqs": snapshot.benchmark_timeout_num_seqs,
        "context_length_capped": snapshot.context_length_capped,
        "context_length_capped_reason": snapshot.context_length_capped_reason,
        "context_length_warnings": [warning.model_dump() for warning in snapshot.context_length_warnings],
        "kv_token_size": snapshot.kv_token_size,
        "server_max_model_len": snapshot.server_max_model_len,
        # The deploy tab reads these two back to rebuild the docker command.
        "selected_max_num_seqs": snapshot.selected_max_num_seqs,
        "selected_max_model_len": snapshot.selected_max_model_len,
    }
    with db_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO profiler_artifacts
            (model_name, vllm_version, config_json, created_at, profiling_results_json, docker_command)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.config.model_name,
                snapshot.config.image_tag,
                snapshot.config.model_dump_json(),
                dt.datetime.now(dt.UTC).isoformat(),
                json.dumps(profiling_results),
                snapshot.docker_command,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


async def run_profiler_job(job: ProfilerJob) -> None:
    global active_job_id
    profiler: LlmProfiler | None = None
    try:
        job.set_status("running")
        profiler = LlmProfiler(job.config, job.container_name, job)
        result = await profiler.run()
        with job.lock:
            job.docker_command = result.docker_command
            job.selected_max_num_seqs = result.selected_max_num_seqs
            job.selected_max_model_len = result.selected_max_model_len
            job.touch()
        persist_job_snapshot(job)
        artifact_id = await asyncio.to_thread(save_artifact, job)
        with job.lock:
            job.artifact_id = artifact_id
            job.touch()
        persist_job_snapshot(job)
        job.set_status("done")
    except asyncio.CancelledError:
        stop_named_containers(job.id)
        for step in job.steps:
            if step.status == "running":
                job.update_step(step.id, "cancelled", error="Cancelled by user")
        job.set_status("cancelled", "Cancelled by user")
    except Exception as exc:
        error_message = exception_debug_message(exc)
        logger.exception("Profiler job %s failed: %s", job.id, error_message)
        # A profiler failure is usually the vLLM engine dying, and its stack trace is in the container,
        # not in ours. The container is normally already gone by now — the sweep unwinds through the
        # teardown in profile() — so fall back on the logs that teardown kept.
        fallback_logs = profiler.last_container_logs if profiler is not None else ""
        error_report, root_cause = await asyncio.to_thread(
            failure_report, job.container_name, traceback.format_exc(), fallback_logs
        )
        # The engine's own exception beats ours as the job's summary: "EngineCore encountered an issue"
        # says nothing, "torch.OutOfMemoryError: CUDA out of memory" says everything.
        error_message = root_cause or error_message
        stop_named_containers(job.id)
        # The step that broke shows the whole report; the job keeps the one-line message, which is what
        # the headers and the deploy/pressure "profiler is running" guards render.
        for step in job.steps:
            if step.status == "running":
                job.update_step(step.id, "error", error=error_report)
                break
        job.set_status("error", error_message)
    finally:
        # Keep the terminal snapshot available for one active-job read so the current frontend can render
        # the final steps. It is already persisted inactive; the read below drops this in-memory copy.
        with job.lock:
            terminal = job.status not in {"queued", "running"}
        if not terminal or active_job_id != job.id:
            active_jobs.pop(job.id, None)
            if active_job_id == job.id:
                active_job_id = None


@router.get("/api/profiler/defaults", response_model=ProfilerDefaults)
async def profiler_defaults() -> ProfilerDefaults:
    return ProfilerDefaults()


@router.post("/api/profiler/jobs", response_model=ProfilerJobSnapshot)
async def start_profiler_job(config: ProfilerConfig) -> ProfilerJobSnapshot:
    global active_job_id
    # Profiled containers use the same credential as the configured proxy target so a
    # completed profile can be deployed behind that proxy without a second key to manage.
    config = config.model_copy(update={"api_key": read_app_config().proxy.api_key})
    try:
        validate_config(config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid profiler config: {exc}") from exc
    if active_job_id is not None:
        previous_job_id = active_job_id
        active = active_jobs.get(previous_job_id)
        if active is not None:
            with active.lock:
                active_running = active.status in {"queued", "running"}
            if active_running:
                raise HTTPException(status_code=409, detail="A profiler job is already running")
        active_jobs.pop(previous_job_id, None)
        active_job_id = None
    ensure_profiler_dirs()
    job = ProfilerJob(config)
    active_jobs[job.id] = job
    active_job_id = job.id
    persist_job_snapshot(job, active=True)
    spawn_background_task(run_profiler_job(job), f"profiler-job-{job.id}")
    return job.snapshot()


@router.get("/api/profiler/jobs/active", response_model=ProfilerJobSnapshot | None)
async def active_profiler_job() -> ProfilerJobSnapshot | None:
    global active_job_id
    if active_job_id is not None:
        job = active_jobs.get(active_job_id)
        if job is not None:
            snapshot = job.snapshot()
            if snapshot.status not in {"queued", "running"}:
                active_jobs.pop(job.id, None)
                active_job_id = None
            return snapshot
        active_job_id = None
    return await asyncio.to_thread(load_active_job_snapshot)


@router.get("/api/profiler/jobs/{job_id}", response_model=ProfilerJobSnapshot)
async def profiler_job(job_id: str) -> ProfilerJobSnapshot:
    job = active_jobs.get(job_id)
    if job is not None:
        return job.snapshot()
    snapshot = await asyncio.to_thread(load_job_snapshot, job_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Profiler job not found")
    return snapshot


def _mark_stored_job_cancelled(snapshot: ProfilerJobSnapshot, cancelled_at: str) -> None:
    with db_connection() as conn:
        conn.execute(
            "UPDATE profiler_jobs SET status = ?, updated_at = ?, active = 0, snapshot_json = ? WHERE id = ?",
            (snapshot.status, cancelled_at, snapshot.model_dump_json(), snapshot.id),
        )
        conn.commit()


@router.post("/api/profiler/jobs/{job_id}/cancel", response_model=ProfilerJobSnapshot)
async def cancel_profiler_job(job_id: str) -> ProfilerJobSnapshot:
    global active_job_id
    job = active_jobs.get(job_id)
    if job is None:
        snapshot = await asyncio.to_thread(load_job_snapshot, job_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="Profiler job not found")
        cancelled_at = dt.datetime.now(dt.UTC).isoformat()
        updated = snapshot.model_copy(update={"status": "cancelled", "error": "Cancelled by user", "updated_at": cancelled_at})
        await asyncio.to_thread(_mark_stored_job_cancelled, updated, cancelled_at)
        stop_named_containers(job_id)
        if active_job_id == job_id:
            active_job_id = None
        return updated
    proc = job.request_cancel()
    if proc is not None and proc.poll() is None:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)
        if proc.poll() is None:
            proc.kill()
    stop_named_containers(job.id)
    for step in job.steps:
        if step.status == "running":
            job.update_step(step.id, "cancelled", error="Cancelled by user")
    job.set_status("cancelled", "Cancelled by user")
    if active_job_id == job.id:
        active_job_id = None
    return job.snapshot()


@router.post("/api/profiler/jobs/{job_id}/skip-benchmark", response_model=ProfilerJobSnapshot)
async def skip_profiler_benchmark(job_id: str) -> ProfilerJobSnapshot:
    """Drop every server still to be benchmarked. The benchmark only produces the charts behind the
    concurrency choice, so the job keeps the points collected so far and moves straight on to that step."""
    job = active_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Profiler job not found")
    with job.lock:
        if job.status not in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="Profiler job is not running")
        if not job.benchmark_skippable:
            raise HTTPException(
                status_code=409,
                detail="The benchmark cannot be skipped until one --max-num-seqs has been benchmarked.",
            )
    job.request_skip_benchmark()
    return job.snapshot()


@router.post("/api/profiler/jobs/{job_id}/oom-recovery", response_model=ProfilerJobSnapshot)
async def choose_profiler_oom_recovery(job_id: str, selection: OomRecoverySelection) -> ProfilerJobSnapshot:
    """Resolve a sweep-time CUDA OOM without pretending vLLM's generic API error is actionable."""
    job = active_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Profiler job not found")
    with job.lock:
        if job.status not in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="Profiler job is not running")
        if not job.awaiting_oom_recovery or job.oom_recovery is None:
            raise HTTPException(status_code=409, detail="Profiler is not waiting for an out-of-memory recovery choice")
        if selection.action in ("deploy", "save_retry"):
            if selection.max_num_seqs is None or selection.max_model_len is None:
                raise HTTPException(status_code=400, detail="max_num_seqs and max_model_len are required to use working values")
            if selection.action == "deploy":
                job.selected_max_num_seqs = selection.max_num_seqs
                job.selected_max_model_len = selection.max_model_len
            else:
                # save_retry saves the completed bench with these values, then the sweep restarts smaller.
                job.oom_recovery_values = (selection.max_num_seqs, selection.max_model_len)
        job.oom_recovery_action = selection.action
        job.awaiting_oom_recovery = False
        job.oom_recovery = None
        job.touch()
    job.oom_recovery_event.set()
    persist_job_snapshot(job)
    return job.snapshot()


@router.post("/api/profiler/jobs/{job_id}/choose-deploy", response_model=ProfilerJobSnapshot)
async def choose_profiler_deploy_config(job_id: str, selection: DeploySelection) -> ProfilerJobSnapshot:
    job = active_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Profiler job not found")
    with job.lock:
        if job.status not in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="Profiler job is not running")
        if not job.awaiting_deploy_config:
            raise HTTPException(status_code=409, detail="Profiler is not waiting for a deploy configuration")
        if job.selected_max_num_seqs is not None:
            raise HTTPException(status_code=409, detail="The deploy configuration has already been selected")
        job.selected_max_num_seqs = selection.max_num_seqs
        job.selected_max_model_len = selection.max_model_len
        job.touch()
    job.deploy_config_event.set()
    persist_job_snapshot(job)
    return job.snapshot()


def list_artifact_summaries() -> list[ProfilerArtifactSummary]:
    with db_connection() as conn:
        rows = conn.execute("SELECT id, model_name, vllm_version, created_at, docker_command FROM profiler_artifacts ORDER BY id DESC").fetchall()
    return [ProfilerArtifactSummary(**dict(row)) for row in rows]


def restore_context_length_warnings(profiling_results: dict[str, Any]) -> dict[str, Any]:
    """Give artifacts saved by older builds the warnings — and the wording — the frontend now renders.
    Those builds stored only the last cap, and none of them stored a message."""
    warnings = profiling_results.get("context_length_warnings") or []
    capped = profiling_results.get("context_length_capped")
    if not warnings and capped is not None:
        warnings = [{"max_model_len": capped, "reason": profiling_results.get("context_length_capped_reason") or "oom"}]
    profiling_results["context_length_warnings"] = [
        ContextLengthWarning(
            **{**warning, "message": warning.get("message") or legacy_context_length_warning_message(
                warning["max_model_len"], warning.get("reason") or "oom"
            )}
        ).model_dump()
        for warning in warnings
    ]
    return profiling_results


def load_artifact(artifact_id: int) -> ProfilerArtifact | None:
    with db_connection() as conn:
        row = conn.execute("SELECT * FROM profiler_artifacts WHERE id = ?", (artifact_id,)).fetchone()
    if row is None:
        return None
    return ProfilerArtifact(
        id=int(row["id"]),
        model_name=row["model_name"],
        vllm_version=row["vllm_version"],
        config=json.loads(row["config_json"]),
        created_at=row["created_at"],
        profiling_results=restore_context_length_warnings(json.loads(row["profiling_results_json"])),
        docker_command=row["docker_command"],
    )


@router.get("/api/profiler/artifacts", response_model=list[ProfilerArtifactSummary])
async def profiler_artifacts() -> list[ProfilerArtifactSummary]:
    return await asyncio.to_thread(list_artifact_summaries)


@router.get("/api/profiler/artifacts/{artifact_id}", response_model=ProfilerArtifact)
async def profiler_artifact(artifact_id: int) -> ProfilerArtifact:
    artifact = await asyncio.to_thread(load_artifact, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Profiler artifact not found")
    return artifact


def delete_artifact(artifact_id: int) -> bool:
    with db_connection() as conn:
        cur = conn.execute("DELETE FROM profiler_artifacts WHERE id = ?", (artifact_id,))
        conn.commit()
        return cur.rowcount > 0


@router.delete("/api/profiler/artifacts/{artifact_id}")
async def delete_profiler_artifact(artifact_id: int) -> dict[str, int]:
    deleted = await asyncio.to_thread(delete_artifact, artifact_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Profiler artifact not found")
    return {"deleted": artifact_id}
