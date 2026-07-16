// Job-time model fitted to the sweep's measurements.
//
// Two quantities drive everything, both read off the bench grid rather than assumed:
//  - prefill time per request, a(P): from the felt TTFT of the least concurrent batches — prefill
//    is compute-bound and serializes whatever --max-num-seqs allows, so it is a property of the
//    prompt length alone;
//  - decode step time, d(B, P): inferred from measured mean ITL after removing the prefill stalls
//    already included in that mean. It grows with decode batch B = min(C, S) and prompt length P.
//
// A synchronized batch of C requests against a --max-num-seqs = S server then runs in
// ceil(C / S) waves — full waves of S plus a possible smaller remainder, each billed at its own
// size's step time: duration ≈ C·a(P) + (N-1)·Σ d(size_w, P).

import type { BenchPoint } from "../types";

export type JobInputs = {
  maxNumSeqs: number;
  concurrentRequests: number;
  promptTokens: number;
  completionTokens: number;
};

export type JobEstimate = {
  // Whole-job seconds, decomposed: every prefill serializes; decode runs in waves.
  prefillSeconds: number;
  decodeSeconds: number;
  durationSeconds: number;
  waves: number;
  // Median wait to first token and mean streaming latency across the batch.
  feltTtft: number;
  feltItl: number;
  prefillPerRequest: number;
  // min(C, S): how many sequences decode together in the full waves.
  decodeBatch: number;
  // True when the inputs land outside the measured prompt lengths or batch sizes.
  extrapolated: boolean;
};

// One value per measured prompt length, aggregated across the batches that share a series.
type CurvePoint = { promptTokens: number; value: number };

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 1 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

function mean(values: number[]): number {
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

// Collapse points to one value per prompt length (medians), sorted ascending. series_id names the
// grid row: the measured median_prompt_tokens wobbles by a few tokens between batches of a series.
function curveOf(points: BenchPoint[], value: (point: BenchPoint) => number | undefined): CurvePoint[] {
  const buckets = new Map<string, { promptTokens: number[]; values: number[] }>();
  for (const point of points) {
    const v = value(point);
    if (v == null || !Number.isFinite(v) || v <= 0) continue;
    let bucket = buckets.get(point.series_id);
    if (!bucket) {
      bucket = { promptTokens: [], values: [] };
      buckets.set(point.series_id, bucket);
    }
    bucket.promptTokens.push(point.median_prompt_tokens);
    bucket.values.push(v);
  }
  return [...buckets.values()]
    .map((bucket) => ({ promptTokens: median(bucket.promptTokens), value: median(bucket.values) }))
    .sort((a, b) => a.promptTokens - b.promptTokens);
}

// Log-log linear interpolation: prefill time and decode step time are near power laws of the prompt
// length, so straight lines in log space track them between the sparse measured lengths. Beyond the
// longest measured prompt the last segment's slope is extended and the result flagged.
function interpolate(
  curve: CurvePoint[],
  promptTokens: number,
): { value: number; extrapolated: boolean } | null {
  if (curve.length === 0) return null;
  if (curve.length === 1) return { value: curve[0].value, extrapolated: true };
  const p = Math.max(promptTokens, 1);
  const first = curve[0];
  if (p <= first.promptTokens) {
    // Below the shortest measured prompt the fixed overhead dominates: clamp rather than extend a
    // power law toward zero.
    return { value: first.value, extrapolated: p < first.promptTokens };
  }
  let hi = curve.findIndex((c) => c.promptTokens >= p);
  const extrapolated = hi === -1;
  if (hi <= 0) hi = curve.length - 1;
  const lo = hi - 1;
  const a = curve[lo];
  const b = curve[hi];
  const t = (Math.log(p) - Math.log(a.promptTokens)) / (Math.log(b.promptTokens) - Math.log(a.promptTokens));
  return { value: Math.exp(Math.log(a.value) + t * (Math.log(b.value) - Math.log(a.value))), extrapolated };
}

// Per-request prefill time from the least concurrent batches: the median of a C-deep synchronized
// batch waits about (C + 1) / 2 serialized prefills, so divide that factor out. All server groups
// contribute — prefill does not care about --max-num-seqs.
function prefillCurve(points: BenchPoint[]): { curve: CurvePoint[]; concurrency: number } | null {
  const usable = points.filter((point) => point.median_ttft > 0);
  if (usable.length === 0) return null;
  const concurrency = Math.min(...usable.map((point) => point.concurrent_requests));
  const curve = curveOf(
    usable.filter((point) => point.concurrent_requests === concurrency),
    (point) => point.median_ttft,
  );
  return curve.length > 0 ? { curve, concurrency } : null;
}

type PrefillCurve = NonNullable<ReturnType<typeof prefillCurve>>;

function prefillPerRequestAt(
  prefill: PrefillCurve,
  promptTokens: number,
): { value: number; extrapolated: boolean } | null {
  const result = interpolate(prefill.curve, promptTokens);
  return result
    ? { value: result.value / ((prefill.concurrency + 1) / 2), extrapolated: result.extrapolated }
    : null;
}

function decodeBatchOf(point: BenchPoint): number {
  // Without a recorded --max-num-seqs (legacy artifacts) assume the server admitted the whole batch.
  return Math.min(point.concurrent_requests, point.max_num_seqs ?? point.concurrent_requests);
}

// Average number of prefills that happen after a request's first token. Requests run in waves of
// --max-num-seqs, and position k in a wave of size W waits through W-k later prefills.
function meanRemainingPrefills(point: BenchPoint): number {
  const seqs = Math.max(1, point.max_num_seqs ?? point.concurrent_requests);
  let total = 0;
  for (let remaining = point.concurrent_requests; remaining > 0; remaining -= seqs) {
    const size = Math.min(seqs, remaining);
    total += (size * (size - 1)) / 2;
  }
  return total / point.concurrent_requests;
}

// Infer the decode-only step time from felt mean ITL. Mean ITL contains the slow gaps caused by
// later prefills in the same wave, so remove that measured prefill cost before fitting decode.
function inferredDecodeItl(point: BenchPoint, prefill: PrefillCurve): number | undefined {
  if (point.completion_tokens == null || point.completion_tokens < 2 || point.average_itl <= 0) return undefined;
  const pointPrefill = prefillPerRequestAt(prefill, point.median_prompt_tokens);
  if (!pointPrefill) return undefined;
  const prefillStall = (meanRemainingPrefills(point) * pointPrefill.value) / (point.completion_tokens - 1);
  const decodeItl = point.average_itl - prefillStall;
  return decodeItl > 0 ? decodeItl : undefined;
}

// The inferred decode-only step time d(batch, prompt length). Every (C, S) cell contributes at
// batch min(C, S). Missing batch sizes are log-log interpolated between measured neighbours;
// outside the measured range the nearest batch stands in and the result is flagged.
function decodeStep(
  points: BenchPoint[],
  batch: number,
  promptTokens: number,
  prefill: PrefillCurve,
): { value: number; extrapolated: boolean } | null {
  const usable = points.filter((point) => inferredDecodeItl(point, prefill) != null);
  if (usable.length === 0) return null;
  const batches = [...new Set(usable.map(decodeBatchOf))].sort((a, b) => a - b);
  const at = (measured: number) =>
    interpolate(
      curveOf(
        usable.filter((point) => decodeBatchOf(point) === measured),
        (point) => inferredDecodeItl(point, prefill),
      ),
      promptTokens,
    );
  if (batches.includes(batch)) return at(batch);
  const lower = batches.filter((candidate) => candidate < batch).pop();
  const upper = batches.find((candidate) => candidate > batch);
  if (lower == null || upper == null) {
    const nearest = lower ?? upper;
    if (nearest == null) return null;
    const result = at(nearest);
    return result ? { value: result.value, extrapolated: true } : null;
  }
  const lowerAt = at(lower);
  const upperAt = at(upper);
  if (!lowerAt || !upperAt) return null;
  const t = (Math.log(batch) - Math.log(lower)) / (Math.log(upper) - Math.log(lower));
  return {
    value: Math.exp(Math.log(lowerAt.value) + t * (Math.log(upperAt.value) - Math.log(lowerAt.value))),
    extrapolated: lowerAt.extrapolated || upperAt.extrapolated,
  };
}

export function estimateJob(points: BenchPoint[], inputs: JobInputs): JobEstimate | null {
  const seqs = Math.max(1, Math.floor(inputs.maxNumSeqs));
  const concurrent = Math.max(1, Math.floor(inputs.concurrentRequests));
  const promptTokens = Math.max(1, inputs.promptTokens);
  const completionTokens = Math.max(2, inputs.completionTokens);

  const prefill = prefillCurve(points);
  if (!prefill) return null;
  const prefillAt = prefillPerRequestAt(prefill, promptTokens);
  if (!prefillAt) return null;
  const prefillPerRequest = prefillAt.value;

  // Wave sizes: full waves of --max-num-seqs, then whatever remains. Each wave decodes at its own
  // size's step time — billing a straggler wave of 1 at the full batch's slower rate would punish a
  // batch size that does not divide the concurrency (e.g. S=3 at C=4) twice for its poor fit.
  const waveSizes: number[] = [];
  for (let remaining = concurrent; remaining > 0; remaining -= seqs) {
    waveSizes.push(Math.min(seqs, remaining));
  }
  const waves = waveSizes.length;
  const gaps = completionTokens - 1;

  let decodeExtrapolated = false;
  const stepCache = new Map<number, number>();
  for (const size of new Set(waveSizes)) {
    const step = decodeStep(points, size, promptTokens, prefill);
    if (!step) return null;
    decodeExtrapolated = decodeExtrapolated || step.extrapolated;
    stepCache.set(size, step.value);
  }
  const stepOfWave = waveSizes.map((size) => stepCache.get(size) ?? NaN);

  const prefillSeconds = concurrent * prefillPerRequest;
  const decodeSeconds = gaps * stepOfWave.reduce((sum, step) => sum + step, 0);

  // Walk every request of the synchronized batch: its wave, its position within it, and how many
  // wave-mates prefill after it starts decoding — their prefills stall its early tokens, which is
  // the transient the felt ITL carries on top of the steady gap.
  const ttfts: number[] = [];
  const feltItls: number[] = [];
  let elapsed = 0;
  for (let wave = 0; wave < waves; wave += 1) {
    const size = waveSizes[wave];
    const step = stepOfWave[wave];
    for (let position = 1; position <= size; position += 1) {
      ttfts.push(elapsed + position * prefillPerRequest);
      feltItls.push(((size - position) * prefillPerRequest + gaps * step) / gaps);
    }
    elapsed += size * prefillPerRequest + gaps * step;
  }

  return {
    prefillSeconds,
    decodeSeconds,
    durationSeconds: prefillSeconds + decodeSeconds,
    waves,
    feltTtft: median(ttfts),
    feltItl: mean(feltItls),
    prefillPerRequest,
    decodeBatch: Math.min(concurrent, seqs),
    extrapolated: prefillAt.extrapolated || decodeExtrapolated,
  };
}

export type MeasuredCell = {
  concurrentRequests: number;
  promptTokens: number;
  completionTokens: number;
  measuredSeconds: number;
  modelSeconds: number;
};

// The measured batch nearest the inputs — same --max-num-seqs, closest concurrency and prompt
// length in log space — with the model recomputed at that cell so the two numbers are comparable.
// This is the calculator showing its work: a large residual means don't trust the estimate.
export function nearestMeasuredCell(points: BenchPoint[], inputs: JobInputs): MeasuredCell | null {
  const candidates = points.filter(
    (point) =>
      point.duration != null &&
      point.duration > 0 &&
      point.completion_tokens != null &&
      point.max_num_seqs === inputs.maxNumSeqs,
  );
  if (candidates.length === 0) return null;
  const distance = (point: BenchPoint) =>
    Math.abs(Math.log(point.concurrent_requests) - Math.log(Math.max(1, inputs.concurrentRequests))) +
    Math.abs(Math.log(point.median_prompt_tokens) - Math.log(Math.max(1, inputs.promptTokens)));
  const nearest = candidates.reduce((best, candidate) =>
    distance(candidate) < distance(best) ? candidate : best,
  );
  const model = estimateJob(points, {
    maxNumSeqs: inputs.maxNumSeqs,
    concurrentRequests: nearest.concurrent_requests,
    promptTokens: nearest.median_prompt_tokens,
    completionTokens: nearest.completion_tokens ?? inputs.completionTokens,
  });
  if (!model) return null;
  return {
    concurrentRequests: nearest.concurrent_requests,
    promptTokens: nearest.median_prompt_tokens,
    completionTokens: nearest.completion_tokens ?? 0,
    measuredSeconds: nearest.duration ?? 0,
    modelSeconds: model.durationSeconds,
  };
}

export function measuredMaxNumSeqsValues(points: BenchPoint[]): number[] {
  const values = new Set<number>();
  for (const point of points) {
    if (point.max_num_seqs != null) values.add(point.max_num_seqs);
  }
  return [...values].sort((a, b) => a - b);
}

export function sweepCompletionTokens(points: BenchPoint[]): number | null {
  const point = points.find((candidate) => candidate.completion_tokens != null);
  return point?.completion_tokens ?? null;
}
