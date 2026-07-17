"""Pressure test: fire a batch of concurrent requests at the served model on demand.

The profiler sweeps --max-num-seqs automatically; this lets you drive the same
machinery by hand at one chosen prompt length and concurrency to see how the
deployed server holds up.
"""

import asyncio
import datetime as dt
import statistics
from time import perf_counter

import httpx
from fastapi import APIRouter, HTTPException
from openai import APIError, APITimeoutError
from pydantic import BaseModel

from backend.config import read_app_config
from backend.deploy import profiler_is_running
from backend.llm_profiler import DEFAULT_COMPLETION_TOKENS, DEFAULT_TTFT_TIMEOUT, vllm_base_url
from backend.vllm_bench_client import RequestSample, VllmBenchClient

router = APIRouter()

MAX_PROMPT_TOKENS = 1_000_000
MAX_NUM_SEQS = 1024
MAX_COMPLETION_TOKENS = 4096


class PressureTestRequest(BaseModel):
    prompt_tokens: int = 1024
    num_seqs: int = 8
    completion_tokens: int = DEFAULT_COMPLETION_TOKENS
    # Read timeout, like the profiler's: how long a request may go without receiving a token
    # before it is given up on. It is not a cap on the batch's total wall-clock time.
    ttft_timeout: int = DEFAULT_TTFT_TIMEOUT


class PressureTestResult(BaseModel):
    model: str
    base_url: str
    started_at: str
    prompt_tokens: int
    num_seqs: int
    completion_tokens: int
    # Wall-clock time for the whole batch, from first request sent to last one finished.
    duration: float
    samples: list[RequestSample]
    median_prompt_tokens: int
    median_ttft: float
    p95_ttft: float
    max_ttft: float
    average_itl: float
    # Aggregate token rate while decoding only, and over the whole batch respectively.
    system_decoding_throughput: float
    system_throughput: float
    failures: int
    error: str = ""


def validate_request(payload: PressureTestRequest) -> None:
    if not 1 <= payload.prompt_tokens <= MAX_PROMPT_TOKENS:
        raise HTTPException(status_code=400, detail=f"prompt_tokens must be between 1 and {MAX_PROMPT_TOKENS}")
    if not 1 <= payload.num_seqs <= MAX_NUM_SEQS:
        raise HTTPException(status_code=400, detail=f"num_seqs must be between 1 and {MAX_NUM_SEQS}")
    if not 1 <= payload.completion_tokens <= MAX_COMPLETION_TOKENS:
        raise HTTPException(status_code=400, detail=f"completion_tokens must be between 1 and {MAX_COMPLETION_TOKENS}")
    if payload.ttft_timeout < 1:
        raise HTTPException(status_code=400, detail="ttft_timeout must be at least 1 second")


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile; statistics.quantiles needs >= 2 points, and a batch can be one."""
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * len(ordered) + 0.5) - 1))
    return ordered[index]


def summarize(
        payload: PressureTestRequest,
        model: str,
        base_url: str,
        started_at: str,
        duration: float,
        samples: list[RequestSample],
        failures: int,
        error: str,
) -> PressureTestResult:
    ttfts = [sample.ttft for sample in samples]
    return PressureTestResult(
        model=model,
        base_url=base_url,
        started_at=started_at,
        prompt_tokens=payload.prompt_tokens,
        num_seqs=payload.num_seqs,
        completion_tokens=payload.completion_tokens,
        duration=duration,
        samples=samples,
        median_prompt_tokens=round(statistics.median(sample.prompt_tokens for sample in samples)) if samples else 0,
        median_ttft=statistics.median(ttfts) if ttfts else 0.0,
        p95_ttft=percentile(ttfts, 0.95) if ttfts else 0.0,
        max_ttft=max(ttfts) if ttfts else 0.0,
        average_itl=statistics.mean(sample.mean_itl for sample in samples) if samples else 0.0,
        system_decoding_throughput=sum(sample.decoding_throughput for sample in samples),
        system_throughput=sum(sample.completion_tokens for sample in samples) / duration if duration else 0.0,
        failures=failures,
        error=error,
    )


@router.post("/api/pressure/run", response_model=PressureTestResult)
async def run_pressure_test(payload: PressureTestRequest) -> PressureTestResult:
    validate_request(payload)
    if await profiler_is_running():
        raise HTTPException(status_code=409, detail="Profiler is running")

    app_config = read_app_config()
    deployment = app_config.deployment
    base_url = vllm_base_url(deployment.port)
    bench = VllmBenchClient(base_url, app_config.proxy.api_key, payload.ttft_timeout)
    try:
        try:
            model = await bench.get_model_name()
        except (APIError, httpx.HTTPError, IndexError) as exc:
            detail = (f"Could not reach the endpoint at {base_url}: {exc}. Did you start a vLLM server? "
                      f"Are the Deployment settings pointing to it?")
            raise HTTPException(status_code=502, detail=detail) from exc

        try:
            prompts = [await bench.detokenized_prompt(model, payload.prompt_tokens) for _ in range(payload.num_seqs)]
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Could not build a {payload.prompt_tokens}-token prompt (is the endpoint vLLM?): {exc}",
            ) from exc

        started_at = dt.datetime.now(dt.UTC).isoformat()
        start = perf_counter()
        # return_exceptions so a batch that partially fails (a request over the context
        # length, a timeout under load) still reports what the surviving requests saw.
        results = await asyncio.gather(
            *(bench.measure_completion(model, prompt, payload.completion_tokens) for prompt in prompts),
            return_exceptions=True,
        )
        duration = perf_counter() - start
    finally:
        await bench.aclose()

    samples = [result for result in results if isinstance(result, RequestSample)]
    errors = [result for result in results if isinstance(result, BaseException)]
    if not samples:
        detail = str(errors[0]) if errors else "No requests completed"
        if errors and isinstance(errors[0], (APITimeoutError, httpx.TimeoutException)):
            detail = f"All {payload.num_seqs} requests went {payload.ttft_timeout}s without a token"
        raise HTTPException(status_code=502, detail=detail)

    return summarize(
        payload,
        model,
        base_url,
        started_at,
        duration,
        samples,
        len(errors),
        str(errors[0]) if errors else "",
    )
