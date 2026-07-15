import asyncio
import random
from time import perf_counter

import httpx
from openai import AsyncOpenAI
from openai.types import Model
from openai.types.chat import (
    ChatCompletionStreamOptionsParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
)
from pydantic import BaseModel


class RequestSample(BaseModel):
    """One request's measurement inside a batch fired at the server."""
    prompt_tokens: int
    completion_tokens: int
    ttft: float
    mean_itl: float
    decoding_throughput: float


class VllmBenchClient:
    """Fires batches of concurrent chat completions at an OpenAI-compatible server and
    measures each one. Shared by the profiler's sweep and the pressure test."""

    def __init__(self, base_url: str, api_key: str, ttft_timeout: float):
        timeout = httpx.Timeout(connect=20.0, read=float(ttft_timeout), write=60.0, pool=20.0)
        self.base_url = base_url.rstrip("/")
        self.http_client = httpx.AsyncClient(timeout=timeout)
        self.client = AsyncOpenAI(
            base_url=f"{self.base_url}/v1",
            api_key=api_key,
            http_client=httpx.AsyncClient(timeout=timeout),
        )

    async def detokenized_prompt(self, model_name: str, n_tokens: int) -> str:
        """Build a prompt of exactly n_tokens by detokenizing random token ids."""
        tokens = [random.randint(100, 1000) for _ in range(n_tokens)]
        resp = await self.http_client.post(
            f"{self.base_url}/detokenize",
            headers={"accept": "application/json", "Content-Type": "application/json"},
            json={
                "model": model_name,
                "tokens": tokens,
            },
        )
        resp.raise_for_status()
        return resp.json()["prompt"]

    async def measure_completion(
            self, model_name: str, prompt: str, completion_tokens: int
    ) -> RequestSample:
        usage = None
        first_token_timestamp = None

        start = perf_counter()
        stream = await self.client.chat.completions.create(
            model=model_name,
            messages=[ChatCompletionSystemMessageParam(role="system", content=""),
                      ChatCompletionUserMessageParam(role="user", content=prompt)],
            max_completion_tokens=completion_tokens,
            stream=True,
            stream_options=ChatCompletionStreamOptionsParam(include_usage=True),
        )
        async for chunk in stream:
            if chunk.usage:
                usage = chunk.usage
            if first_token_timestamp is None and chunk.choices[0] and chunk.choices[0].delta.content:
                first_token_timestamp = perf_counter()

        if not usage or usage.completion_tokens == 0:
            raise RuntimeError("Model generated 0 tokens.")

        end = perf_counter()
        ttft = first_token_timestamp - start
        decoded_tokens = usage.completion_tokens - 1
        decoding_duration = end - first_token_timestamp
        mean_itl = decoding_duration / decoded_tokens if decoded_tokens else 0.0
        decoding_throughput = decoded_tokens / decoding_duration if decoded_tokens else 0.0

        return RequestSample(
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            ttft=ttft,
            mean_itl=mean_itl,
            decoding_throughput=decoding_throughput,
        )

    async def build_prompts(self, model_name: str, num_seqs: int, n_tokens: int) -> list[str]:
        return [await self.detokenized_prompt(model_name, n_tokens) for _ in range(num_seqs)]

    async def run_batch(
            self, model_name: str, prompts: list[str], completion_tokens: int
    ) -> list[RequestSample]:
        """Send num_seqs prompts of n_tokens each, all in flight at once."""
        return list(
            await asyncio.gather(
                *(self.measure_completion(model_name, prompt, completion_tokens) for prompt in prompts)
            )
        )

    async def _served_model(self) -> Model:
        return (await self.client.models.list()).data[0]

    async def get_model_name(self) -> str:
        return (await self._served_model()).id

    async def get_max_model_len(self) -> int:
        return int((await self._served_model()).max_model_len)

    async def aclose(self) -> None:
        await self.http_client.aclose()
        await self.client.close()
