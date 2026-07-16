"""Deployment helpers: GPU/docker probing, image pulls, and runtime control."""

import asyncio
import contextlib
import datetime as dt
import json
import shlex

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend import profiler as profiler_state
from backend.llm_profiler import (
    DEFAULT_VLLM_IMAGE,
    DEPLOY_CONTAINER_NAME,
    LlmProfiler,
    ProfilerConfig,
    build_deploy_cmd,
    is_server_ready,
    vllm_base_url,
)
from backend.tasks import spawn_background_task
from backend.vllm_bench_client import VllmBenchClient

DOCKER_PULL_TIMEOUT_SECONDS = 1800.0
DEPLOY_READY_TIMEOUT_SECONDS = 600.0
DEPLOY_READY_POLL_INTERVAL_SECONDS = 0.5

router = APIRouter()


class GpuStats(BaseModel):
    index: int
    name: str
    utilization_percent: float
    memory_used_mib: float
    memory_total_mib: float


class DockerPullStatus(BaseModel):
    image: str = ""
    state: str = "idle"  # idle | running | success | error
    error: str = ""


class DockerPullRequest(BaseModel):
    image: str = ""


class DeployRuntimeStatus(BaseModel):
    state: str = "idle"  # idle | starting | serving | stopping | error
    artifact_id: int | None = None
    model_name: str = ""
    command: str = ""
    started_at: str = ""
    uptime_seconds: int = 0
    container_name: str = DEPLOY_CONTAINER_NAME
    gpu_busy: bool = False
    error: str = ""


class DeployRuntimeStartRequest(BaseModel):
    artifact_id: int
    command: str | None = None


class DeployCommandPreview(BaseModel):
    command: str


class DeployStatus(BaseModel):
    gpus: list[GpuStats] = []
    gpu_error: str = ""
    docker_images: list[str] = []
    docker_error: str = ""
    docker_pull: DockerPullStatus = Field(default_factory=DockerPullStatus)
    runtime: DeployRuntimeStatus = Field(default_factory=DeployRuntimeStatus)
    default_vllm_image: str = DEFAULT_VLLM_IMAGE


async def run_cli_command(*args: str, timeout: float = 5.0) -> tuple[str, str]:
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return "", f"{args[0]} not found"
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return "", f"{args[0]} timed out"
    if process.returncode != 0:
        return "", stderr.decode(errors="replace").strip() or f"{args[0]} exited with code {process.returncode}"
    return stdout.decode(errors="replace"), ""


async def detect_gpus() -> tuple[list[GpuStats], str]:
    stdout, error = await run_cli_command(
        "nvidia-smi",
        "--query-gpu=index,name,utilization.gpu,memory.used,memory.total",
        "--format=csv,noheader,nounits",
    )
    if error:
        return [], error

    gpus: list[GpuStats] = []
    for line in stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 5:
            continue
        index, name, utilization, memory_used, memory_total = parts
        gpus.append(
            GpuStats(
                index=int(index),
                name=name,
                utilization_percent=float(utilization),
                memory_used_mib=float(memory_used),
                memory_total_mib=float(memory_total),
            )
        )
    return gpus, ""


async def detect_vllm_docker_images() -> tuple[list[str], str]:
    stdout, error = await run_cli_command("docker", "images", "--format", "{{.Repository}}:{{.Tag}}")
    if error:
        return [], error

    images = {
        line.strip()
        for line in stdout.splitlines()
        if line.strip() and not line.endswith(":<none>") and "vllm" in line.lower()
    }
    return sorted(images), ""


# Module-level app state: only read/written from the event loop thread.
docker_pull_status = DockerPullStatus()
runtime_status = DeployRuntimeStatus()


async def run_docker_pull(image: str) -> None:
    global docker_pull_status
    _, error = await run_cli_command("docker", "pull", image, timeout=DOCKER_PULL_TIMEOUT_SECONDS)
    docker_pull_status = DockerPullStatus(image=image, state="error" if error else "success", error=error)


async def profiler_is_running() -> bool:
    if profiler_state.active_job_id is not None:
        job = profiler_state.active_jobs.get(profiler_state.active_job_id)
        if job is not None:
            with job.lock:
                if job.status in {"queued", "running"}:
                    return True
    snapshot = await asyncio.to_thread(profiler_state.load_active_job_snapshot)
    return snapshot is not None and snapshot.status in {"queued", "running"}


class DeployArtifact(BaseModel):
    config: ProfilerConfig
    profiling_results: dict
    model_name: str
    # The launch command as it was saved with the profile. Authoritative: what a profile deploys is
    # decided once, when it is saved, not re-derived from today's code.
    docker_command: str


def load_profiler_artifact(artifact_id: int) -> DeployArtifact:
    with profiler_state.db_connection() as conn:
        row = conn.execute(
            "SELECT model_name, config_json, profiling_results_json, docker_command FROM profiler_artifacts WHERE id = ?",
            (artifact_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Profiler artifact not found")
    return DeployArtifact(
        config=ProfilerConfig.model_validate_json(row["config_json"]),
        profiling_results=json.loads(row["profiling_results_json"]),
        model_name=row["model_name"],
        docker_command=row["docker_command"] or "",
    )


def stored_deploy_command(docker_command: str) -> list[str] | None:
    """The saved launch command, or None when the profile predates them being saved.

    Profiles saved before this stored the *profiler's* command instead — same image, but named after a
    long-gone profiler container and running in the foreground. Those cannot be launched as-is, so they
    are told apart by the container they name and fall back to being rebuilt.
    """
    if not docker_command.strip():
        return None
    try:
        cmd = parse_edited_command(docker_command)
    except HTTPException:
        return None
    return cmd if DEPLOY_CONTAINER_NAME in cmd else None


def build_deploy_command(config: ProfilerConfig, profiling_results: dict, *, ensure_cache_dirs: bool = True) -> list[
    str]:
    """Rebuild the launch command from the profile's settings. Only for profiles saved before the command
    itself was stored — everything else deploys the command it was saved with, via deploy_command()."""
    max_num_seqs = profiling_results.get("selected_max_num_seqs")
    if not isinstance(max_num_seqs, int) or max_num_seqs <= 0:
        raise HTTPException(status_code=400, detail="Profile is missing selected --max-num-seqs")
    # The context length chosen at the configure step. deploy_max_model_len is the name older
    # artifacts stored the same value under, back when it was derived from the KV budget rather
    # than picked outright.
    max_model_len = profiling_results.get("selected_max_model_len")
    if max_model_len is None:
        max_model_len = profiling_results.get("deploy_max_model_len")
    if max_model_len is not None and (not isinstance(max_model_len, int) or max_model_len <= 0):
        raise HTTPException(status_code=400, detail="Profile has invalid --max-model-len")
    return build_deploy_cmd(
        config, max_num_seqs=max_num_seqs, max_model_len=max_model_len, ensure_cache_dirs=ensure_cache_dirs
    )


def deploy_command(artifact: DeployArtifact, *, ensure_cache_dirs: bool = True) -> list[str]:
    """What this profile launches. The command saved with the profile wins: changing how commands are
    built must never change what an already-saved profile deploys."""
    stored = stored_deploy_command(artifact.docker_command)
    if stored is not None:
        if ensure_cache_dirs:
            LlmProfiler.ensure_cache_dirs(artifact.config)
        return stored
    return build_deploy_command(artifact.config, artifact.profiling_results, ensure_cache_dirs=ensure_cache_dirs)


def parse_edited_command(command: str) -> list[str]:
    cmd = shlex.split(command.replace("\\\n", " "))
    if len(cmd) < 2 or cmd[0] != "docker" or cmd[1] != "run":
        raise HTTPException(status_code=400, detail="Command must start with 'docker run'")
    return cmd


async def inspect_runtime_container() -> bool:
    stdout, _ = await run_cli_command(
        "docker",
        "inspect",
        "--format",
        "{{.State.Running}}",
        DEPLOY_CONTAINER_NAME,
    )
    return stdout.strip().lower() == "true"


async def runtime_is_ready(config: ProfilerConfig) -> bool:
    """Use the profiler's full warmup probe for the deployed vLLM instance."""
    bench = VllmBenchClient(vllm_base_url(config.port), config.api_key, config.ttft_timeout)
    try:
        return await is_server_ready(bench)
    finally:
        await bench.aclose()


async def wait_for_runtime_ready(started_at: str, config: ProfilerConfig) -> None:
    """Promote this launch to serving only after vLLM itself is accepting requests."""
    global runtime_status
    deadline = asyncio.get_running_loop().time() + DEPLOY_READY_TIMEOUT_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        # A stop or another launch took ownership of the status while this task was waiting.
        if runtime_status.state != "starting" or runtime_status.started_at != started_at:
            return
        if not await inspect_runtime_container():
            logs, _ = await run_cli_command("docker", "logs", "--tail", "50", DEPLOY_CONTAINER_NAME, timeout=10)
            detail = logs.strip() or "Container exited before it became ready"
            if runtime_status.state == "starting" and runtime_status.started_at == started_at:
                runtime_status = runtime_status.model_copy(update={"state": "error", "error": detail})
            return
        if await runtime_is_ready(config):
            if runtime_status.state == "starting" and runtime_status.started_at == started_at:
                runtime_status = runtime_status.model_copy(update={"state": "serving", "error": ""})
            return
        await asyncio.sleep(DEPLOY_READY_POLL_INTERVAL_SECONDS)

    if runtime_status.state == "starting" and runtime_status.started_at == started_at:
        runtime_status = runtime_status.model_copy(
            update={"state": "error", "error": "vLLM did not become ready before the deployment timed out"}
        )


async def refresh_runtime_state() -> None:
    """Reconcile the in-memory runtime state with the actual container.

    This is the one place the runtime state machine reacts to the container
    dying or appearing outside our control.
    """
    global runtime_status
    running = await inspect_runtime_container()
    if runtime_status.state in {"serving", "stopping"} and not running:
        runtime_status = DeployRuntimeStatus(
            state="idle",
            artifact_id=runtime_status.artifact_id,
            model_name=runtime_status.model_name,
            command=runtime_status.command,
            container_name=DEPLOY_CONTAINER_NAME,
        )
    elif running and runtime_status.state not in {"starting", "serving", "stopping"}:
        runtime_status = runtime_status.model_copy(update={"state": "serving"})


def runtime_status_view(gpus: list[GpuStats] | None = None) -> DeployRuntimeStatus:
    """Pure view of the current runtime state with derived uptime/GPU fields."""
    started_at = runtime_status.started_at
    uptime_seconds = 0
    if started_at:
        with contextlib.suppress(ValueError):
            started = dt.datetime.fromisoformat(started_at)
            uptime_seconds = max(0, int((dt.datetime.now(dt.UTC) - started).total_seconds()))

    gpu_busy = any(gpu.utilization_percent > 5 or gpu.memory_used_mib > 1024 for gpu in (gpus or []))
    return runtime_status.model_copy(update={"uptime_seconds": uptime_seconds, "gpu_busy": gpu_busy})


@router.get("/api/deploy/status", response_model=DeployStatus)
async def deploy_status() -> DeployStatus:
    (gpus, gpu_error), (docker_images, docker_error) = await asyncio.gather(
        detect_gpus(),
        detect_vllm_docker_images(),
    )
    await refresh_runtime_state()
    return DeployStatus(
        gpus=gpus,
        gpu_error=gpu_error,
        docker_images=docker_images,
        docker_error=docker_error,
        docker_pull=docker_pull_status,
        runtime=runtime_status_view(gpus),
    )


@router.post("/api/deploy/docker/pull", response_model=DockerPullStatus)
async def start_docker_pull(payload: DockerPullRequest) -> DockerPullStatus:
    global docker_pull_status
    image = payload.image.strip() or DEFAULT_VLLM_IMAGE
    if docker_pull_status.state == "running":
        return docker_pull_status
    # Mark running before yielding control so a concurrent request can't start
    # a second pull.
    docker_pull_status = DockerPullStatus(image=image, state="running")
    spawn_background_task(run_docker_pull(image), name=f"docker-pull-{image}")
    return docker_pull_status


@router.get("/api/deploy/runtime/command", response_model=DeployCommandPreview)
async def preview_runtime_command(artifact_id: int) -> DeployCommandPreview:
    artifact = await asyncio.to_thread(load_profiler_artifact, artifact_id)
    cmd = deploy_command(artifact, ensure_cache_dirs=False)
    return DeployCommandPreview(command=LlmProfiler.pretty_docker_cmd(cmd))


@router.post("/api/deploy/runtime/start", response_model=DeployRuntimeStatus)
async def start_runtime(payload: DeployRuntimeStartRequest) -> DeployRuntimeStatus:
    global runtime_status
    if await profiler_is_running():
        raise HTTPException(status_code=409, detail="Profiler is running")
    if await inspect_runtime_container():
        await refresh_runtime_state()
        return runtime_status_view()

    artifact = await asyncio.to_thread(load_profiler_artifact, payload.artifact_id)
    if payload.command and payload.command.strip():
        # The user edited the command in the UI: theirs wins over the saved one.
        cmd = parse_edited_command(payload.command)
        LlmProfiler.ensure_cache_dirs(artifact.config)
    else:
        cmd = deploy_command(artifact)
    command = LlmProfiler.pretty_docker_cmd(cmd)
    starting = DeployRuntimeStatus(
        state="starting",
        artifact_id=payload.artifact_id,
        model_name=artifact.model_name,
        command=command,
        started_at=dt.datetime.now(dt.UTC).isoformat(),
        container_name=DEPLOY_CONTAINER_NAME,
    )
    runtime_status = starting

    # Remove any exited leftover so `docker run --name` won't conflict.
    await run_cli_command("docker", "rm", "-f", DEPLOY_CONTAINER_NAME, timeout=30)

    _, error = await run_cli_command(*cmd, timeout=30)
    if error:
        runtime_status = starting.model_copy(update={"state": "error", "error": error})
        raise HTTPException(status_code=500, detail=error)

    # `docker run -d` returns as soon as the container is created; a container that
    # exits immediately (bad args, OOM) is not a successful launch.
    if not await inspect_runtime_container():
        logs, _ = await run_cli_command("docker", "logs", "--tail", "50", DEPLOY_CONTAINER_NAME, timeout=10)
        detail = logs.strip() or "Container exited immediately after starting"
        runtime_status = starting.model_copy(update={"state": "error", "error": detail})
        raise HTTPException(status_code=500, detail=detail)

    spawn_background_task(
        wait_for_runtime_ready(starting.started_at, artifact.config),
        name=f"deploy-ready-{payload.artifact_id}",
    )
    return runtime_status_view()


@router.post("/api/deploy/runtime/stop", response_model=DeployRuntimeStatus)
async def stop_runtime() -> DeployRuntimeStatus:
    global runtime_status
    runtime_status = runtime_status.model_copy(update={"state": "stopping"})
    _, error = await run_cli_command("docker", "rm", "-f", DEPLOY_CONTAINER_NAME, timeout=30)
    if error and "No such container" not in error:
        runtime_status = runtime_status.model_copy(update={"state": "error", "error": error})
        raise HTTPException(status_code=500, detail=error)
    runtime_status = DeployRuntimeStatus(
        state="idle",
        artifact_id=runtime_status.artifact_id,
        model_name=runtime_status.model_name,
        command=runtime_status.command,
        container_name=DEPLOY_CONTAINER_NAME,
    )
    return runtime_status
