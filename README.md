
![cover](./cover.jpg)

### The Continuous Verification and Optimization layer for self-hosted LLMs

Plugs into your self-hosted, OpenAI-compatible endpoint (vLLM, SGLang) and re-checks past predictions with a heavier verification pass on idle compute, catching regressions and building a ground-truth record without extra infrastructure.
Optimizes requests on the way through to reduce latency.

[![license](https://img.shields.io/badge/license-Apache--2.0-blue)](https://www.apache.org/licenses/LICENSE-2.0.txt)
[![X](https://img.shields.io/twitter/follow/tschillaciML?logo=X&color=%20%23f5f5f5)](https://x.com/tschillaciML)

## Features

- Drop-in OpenAI-compatible proxy: sits between your workload and your endpoint, no client changes needed.
- Image optimization: optimizes base64 images before they hit your GPU, shrinking payload with no client changes.
- Request tee/logging: every request/response captured to SQLite for replay and auditing.
- Idle-compute verification: re-runs a heavier check on past predictions when the proxy is idle, no added latency.

## Quickstart

```bash
cp backend/.env.template backend/.env  # Then edit the file
npm i
npm run build
npm run start
```

Then point your client at the proxy:

```python
base_url = "http://localhost:8001/v1"
```

## Architecture

![architecture](./architecture.svg)

## Roadmap

- Reasoning models support, with higher reasoning effort on verification passes.
- Latency-aware fidelity tuning: profile per-domain latency and accuracy, then hold the highest image fidelity that fits your end-to-end latency budget
- Self-consistency sampling
- Human corrections hook
- vLLM profiler: use every byte of memory on your hardware to maximize model throughput

## Contact

Running open-weight inference in production? I want to hear what you're running!
[@tschillaciml](https://x.com/tschillaciml)

Leave a star if this is helpful ♥️
