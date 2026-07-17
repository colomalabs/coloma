import asyncio

import pytest
from fastapi.testclient import TestClient

from backend import main, pressure
from backend.config import AppConfig, DeploymentConfig, ProxyConfig
from backend.vllm_bench_client import RequestSample


class FakeBenchClient:
    """Stands in for VllmBenchClient: records the batch it was asked to fire."""

    instances: list["FakeBenchClient"] = []

    def __init__(self, base_url, api_key, ttft_timeout, samples=None, failures=0):
        self.base_url = base_url
        self.api_key = api_key
        self.ttft_timeout = ttft_timeout
        self.prompts_built = 0
        self.in_flight = 0
        self.peak_in_flight = 0
        self.closed = False
        FakeBenchClient.instances.append(self)

    async def get_model_name(self):
        return "fake/model"

    async def detokenized_prompt(self, model_name, n_tokens):
        self.prompts_built += 1
        return "tok " * n_tokens

    async def measure_completion(self, model_name, prompt, completion_tokens):
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0)
            index = self.in_flight
            # mean_itl of 0.1s => a decode rate of 10 tok/s per request.
            return RequestSample(prompt_tokens=512, completion_tokens=10, ttft=0.1 * index, mean_itl=0.1,
                                 decoding_throughput=10.0)
        finally:
            self.in_flight -= 1

    async def aclose(self):
        self.closed = True


@pytest.fixture(autouse=True)
def fake_bench(monkeypatch):
    FakeBenchClient.instances.clear()
    monkeypatch.setattr(pressure, "VllmBenchClient", FakeBenchClient)
    monkeypatch.setattr(
        pressure,
        "read_app_config",
        lambda: AppConfig(
            proxy=ProxyConfig(base_url="http://remote-proxy-target:9999", api_key="proxy-key"),
            deployment=DeploymentConfig(port=9123),
        ),
    )

    async def not_running():
        return False

    monkeypatch.setattr(pressure, "profiler_is_running", not_running)


def run_pressure(**payload):
    with TestClient(main.app) as client:
        return client.post("/api/pressure/run", json=payload)


def test_pressure_run_fires_the_whole_batch_concurrently():
    response = run_pressure(prompt_tokens=512, num_seqs=4, completion_tokens=10)

    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "fake/model"
    assert body["num_seqs"] == 4
    assert len(body["samples"]) == 4
    assert body["failures"] == 0
    # All four requests are in flight at once, not sent one after another.
    bench = FakeBenchClient.instances[0]
    assert bench.base_url == "http://localhost:9123"
    assert bench.api_key == "proxy-key"
    assert body["base_url"] == "http://localhost:9123"
    assert bench.peak_in_flight == 4
    assert bench.prompts_built == 4
    assert bench.closed


def test_pressure_run_summarizes_ttft_itl_and_throughput():
    response = run_pressure(prompt_tokens=128, num_seqs=4, completion_tokens=10)

    body = response.json()
    # Samples get ttft 0.1..0.4 and 10 tok/s each.
    assert body["median_ttft"] == pytest.approx(0.25)
    assert body["max_ttft"] == pytest.approx(0.4)
    assert body["p95_ttft"] == pytest.approx(0.4)
    assert body["average_itl"] == pytest.approx(0.1)
    assert body["system_decoding_throughput"] == pytest.approx(40.0)
    assert body["system_throughput"] > 0
    assert body["median_prompt_tokens"] == 512


def test_pressure_run_reports_partial_failures(monkeypatch):
    failing = {"count": 0}
    original = FakeBenchClient.measure_completion

    async def flaky(self, model_name, prompt, completion_tokens):
        failing["count"] += 1
        if failing["count"] == 1:
            raise RuntimeError("engine is out of KV cache")
        return await original(self, model_name, prompt, completion_tokens)

    monkeypatch.setattr(FakeBenchClient, "measure_completion", flaky)

    response = run_pressure(prompt_tokens=128, num_seqs=3, completion_tokens=10)

    assert response.status_code == 200
    body = response.json()
    assert body["failures"] == 1
    assert len(body["samples"]) == 2
    assert "out of KV cache" in body["error"]


def test_pressure_run_fails_when_every_request_fails(monkeypatch):
    async def always_fails(self, model_name, prompt, completion_tokens):
        raise RuntimeError("engine died")

    monkeypatch.setattr(FakeBenchClient, "measure_completion", always_fails)

    response = run_pressure(prompt_tokens=128, num_seqs=2, completion_tokens=10)

    assert response.status_code == 502
    assert "engine died" in response.json()["detail"]


@pytest.mark.parametrize(
    "payload",
    [
        {"prompt_tokens": 0, "num_seqs": 4},
        {"prompt_tokens": 128, "num_seqs": 0},
        {"prompt_tokens": 128, "num_seqs": 4, "completion_tokens": 0},
        {"prompt_tokens": 128, "num_seqs": pressure.MAX_NUM_SEQS + 1},
    ],
)
def test_pressure_run_rejects_out_of_range_settings(payload):
    assert run_pressure(**payload).status_code == 400


def test_pressure_run_refuses_while_the_profiler_holds_the_gpu(monkeypatch):
    async def running():
        return True

    monkeypatch.setattr(pressure, "profiler_is_running", running)

    response = run_pressure(prompt_tokens=128, num_seqs=2)

    assert response.status_code == 409
    assert response.json()["detail"] == "Profiler is running"
