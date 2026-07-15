import asyncio
import json

import pytest
from fastapi import HTTPException

from backend import deploy, profiler
from backend.llm_profiler import ProfilerConfig


def save_fake_artifact(tmp_path, monkeypatch, config: ProfilerConfig | None = None, profiling_results: dict | None = None) -> int:
    monkeypatch.setattr(profiler, "PROFILER_DB_PATH", tmp_path / "profiler.sqlite3")
    config = config or ProfilerConfig(model_name="test-model")
    profiling_results = profiling_results or {
        "selected_max_model_len": 2048,
        "selected_max_num_seqs": 4,
    }
    with profiler.db_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO profiler_artifacts
            (model_name, vllm_version, config_json, created_at, profiling_results_json, docker_command)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                config.model_name,
                config.image_tag,
                config.model_dump_json(),
                "2026-01-01T00:00:00+00:00",
                json.dumps(profiling_results),
                "docker run old-display-command",
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def test_build_deploy_command_uses_profile_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    config = ProfilerConfig(model_name="test-model", api_key="key", port=9123, fp8=False)

    cmd = deploy.build_deploy_command(
        config,
        {"selected_max_model_len": 4096, "selected_max_num_seqs": 8},
    )

    assert cmd[:3] == ["docker", "run", "-d"]
    assert deploy.DEPLOY_CONTAINER_NAME in cmd
    assert "--max-num-seqs" in cmd
    assert cmd[cmd.index("--max-num-seqs") + 1] == "8"
    assert "--max-model-len" in cmd
    assert cmd[cmd.index("--max-model-len") + 1] == "4096"
    assert "--kv-cache-dtype" not in cmd
    assert cmd[cmd.index("-p") + 1] == "9123:8000"
    assert cmd[cmd.index("--api-key") + 1] == "key"


def test_build_deploy_command_requires_max_num_seqs():
    with pytest.raises(HTTPException) as exc_info:
        deploy.build_deploy_command(ProfilerConfig(model_name="test-model"), {})

    assert exc_info.value.status_code == 400


def test_start_runtime_rejects_running_profiler(monkeypatch, tmp_path):
    monkeypatch.setattr(profiler, "PROFILER_DB_PATH", tmp_path / "profiler.sqlite3")
    job = profiler.ProfilerJob(ProfilerConfig(model_name="test-model"))
    job.status = "running"
    profiler.active_jobs[job.id] = job
    profiler.active_job_id = job.id
    try:
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(deploy.start_runtime(deploy.DeployRuntimeStartRequest(artifact_id=1)))

        assert exc_info.value.status_code == 409
    finally:
        profiler.active_jobs.pop(job.id, None)
        profiler.active_job_id = None


def test_start_runtime_errors_when_container_exits_immediately(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    artifact_id = save_fake_artifact(tmp_path, monkeypatch)

    async def fake_run_cli_command(*args, timeout=5.0):
        if args[:2] == ("docker", "logs"):
            return "CUDA out of memory.\n", ""
        return "", ""

    async def fake_inspect_runtime_container():
        return False

    monkeypatch.setattr(deploy, "run_cli_command", fake_run_cli_command)
    monkeypatch.setattr(deploy, "inspect_runtime_container", fake_inspect_runtime_container)
    deploy.runtime_status = deploy.DeployRuntimeStatus()

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(deploy.start_runtime(deploy.DeployRuntimeStartRequest(artifact_id=artifact_id)))

    assert exc_info.value.status_code == 500
    assert deploy.runtime_status.state == "error"
    assert "CUDA out of memory." in deploy.runtime_status.error


def test_stop_runtime_ignores_missing_container(monkeypatch):
    async def fake_run_cli_command(*args, timeout=5.0):
        return "", "Error: No such container: coloma-deploy"

    monkeypatch.setattr(deploy, "run_cli_command", fake_run_cli_command)
    deploy.runtime_status = deploy.DeployRuntimeStatus(state="serving", artifact_id=1, model_name="test-model", command="docker run")

    status = asyncio.run(deploy.stop_runtime())

    assert status.state == "idle"
    assert status.artifact_id == 1


SAVED_COMMAND = (
    "docker run \\\n"
    "    -d \\\n"
    "    --restart unless-stopped \\\n"
    "    --runtime nvidia \\\n"
    "    --gpus device=0 \\\n"
    f"    --name {deploy.DEPLOY_CONTAINER_NAME} \\\n"
    "    vllm/vllm-openai:v0.25.0 \\\n"
    "    test-model \\\n"
    "    --max-num-seqs 4 \\\n"
    "    --max-model-len 2048"
)


def deploy_artifact(docker_command: str) -> deploy.DeployArtifact:
    return deploy.DeployArtifact(
        config=ProfilerConfig(model_name="test-model"),
        profiling_results={"selected_max_model_len": 2048, "selected_max_num_seqs": 4},
        model_name="test-model",
        docker_command=docker_command,
    )


def test_a_saved_profile_deploys_the_command_it_was_saved_with(monkeypatch):
    # The whole point: what a saved profile launches was decided when it was saved. Whatever
    # build_docker_cmd becomes later must not reach back and change it.
    def poisoned_build(*args, **kwargs):
        raise AssertionError("a saved command must not be rebuilt from today's build_docker_cmd")

    monkeypatch.setattr(deploy.LlmProfiler, "build_docker_cmd", staticmethod(poisoned_build))
    monkeypatch.setattr(deploy.LlmProfiler, "ensure_cache_dirs", staticmethod(lambda config: None))

    cmd = deploy.deploy_command(deploy_artifact(SAVED_COMMAND))

    assert cmd[:3] == ["docker", "run", "-d"]
    assert cmd[cmd.index("--max-num-seqs") + 1] == "4"
    assert deploy.DEPLOY_CONTAINER_NAME in cmd


def test_a_profile_saved_before_launch_commands_were_stored_is_rebuilt(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    # Older profiles stored the profiler's own command: a foreground run of a long-gone profiler
    # container. It cannot be launched, so it is rebuilt from the profile's settings instead.
    profiler_command = "docker run \\\n    --name coloma-profiler-abc123 \\\n    vllm/vllm-openai:v0.25.0"
    cmd = deploy.deploy_command(deploy_artifact(profiler_command))

    assert deploy.DEPLOY_CONTAINER_NAME in cmd
    assert "coloma-profiler-abc123" not in cmd
    assert cmd[:3] == ["docker", "run", "-d"]


def test_a_profile_with_no_saved_command_is_rebuilt(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    cmd = deploy.deploy_command(deploy_artifact(""))

    assert cmd[:3] == ["docker", "run", "-d"]
    assert deploy.DEPLOY_CONTAINER_NAME in cmd
