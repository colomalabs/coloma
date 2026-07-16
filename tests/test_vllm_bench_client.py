import asyncio
from types import SimpleNamespace

import pytest

from backend import vllm_bench_client
from backend.vllm_bench_client import VllmBenchClient


def make_chunk(content: str | None = None, usage: SimpleNamespace | None = None) -> SimpleNamespace:
    choice = SimpleNamespace(delta=SimpleNamespace(content=content))
    return SimpleNamespace(usage=usage, choices=[choice] if content is not None else [])


class FakeStream:
    def __init__(self, chunks):
        self._chunks = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._chunks)
        except StopIteration:
            raise StopAsyncIteration from None


def test_measure_completion_splits_felt_and_steady_state_itl(monkeypatch):
    # perf_counter values consumed in order: request start, one per content chunk, stream end.
    # The 0.9s gap is a decode stalled behind a batch-mate's prefill: it belongs in the felt mean
    # but must not move the steady-state median.
    timestamps = iter([0.0, 1.0, 1.1, 2.0, 2.1, 2.2, 3.0])
    monkeypatch.setattr(vllm_bench_client, "perf_counter", lambda: next(timestamps))

    usage = SimpleNamespace(prompt_tokens=512, completion_tokens=5)
    chunks = [make_chunk(content="x") for _ in range(5)] + [make_chunk(usage=usage)]
    created: dict = {}

    async def fake_create(**kwargs):
        created.update(kwargs)
        return FakeStream(chunks)

    client = VllmBenchClient("http://localhost:8000", "key", ttft_timeout=30)
    monkeypatch.setattr(client.client.chat.completions, "create", fake_create)

    async def scenario():
        try:
            return await client.measure_completion("test-model", "prompt", 5)
        finally:
            await client.aclose()

    sample = asyncio.run(scenario())

    assert sample.prompt_tokens == 512
    assert sample.completion_tokens == 5
    assert sample.ttft == pytest.approx(1.0)
    # 4 decoded tokens over the (3.0 - 1.0)s after the first one.
    assert sample.mean_itl == pytest.approx(0.5)
    # Gaps are 0.1, 0.9, 0.1, 0.1: the stall is excluded from the median.
    assert sample.median_itl == pytest.approx(0.1)
    assert sample.decoding_throughput == pytest.approx(2.0)
    # Random-token prompts can hit EOS early; the bench must decode the full completion length.
    assert created["extra_body"] == {"ignore_eos": True}


def test_measure_completion_rejects_a_response_with_no_tokens(monkeypatch):
    monkeypatch.setattr(vllm_bench_client, "perf_counter", lambda: 0.0)
    usage = SimpleNamespace(prompt_tokens=512, completion_tokens=0)

    async def fake_create(**kwargs):
        return FakeStream([make_chunk(usage=usage)])

    client = VllmBenchClient("http://localhost:8000", "key", ttft_timeout=30)
    monkeypatch.setattr(client.client.chat.completions, "create", fake_create)

    async def scenario():
        try:
            await client.measure_completion("test-model", "prompt", 5)
        finally:
            await client.aclose()

    with pytest.raises(RuntimeError, match="0 tokens"):
        asyncio.run(scenario())
