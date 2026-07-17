import { describe, expect, it } from "vitest";
import type { BenchPoint } from "../types";
import {
  estimateJob,
  measuredMaxNumSeqsValues,
  nearestMeasuredCell,
  sweepCompletionTokens,
  type JobInputs,
} from "./jobModel";

const COMPLETION_TOKENS = 3;

// A synthetic benchmark generated from known prefill/decode curves. Keeping the
// source curves simple makes the expected interpolation and wave costs exact:
//   prefill(P) = P / 100
//   decodeStep(batch, P) = 0.1 * batch * sqrt(P / 100)
function benchmarkPoint(promptTokens: number, concurrentRequests: number, maxNumSeqs = 4): BenchPoint {
  const prefill = promptTokens / 100;
  const decodeBatch = Math.min(concurrentRequests, maxNumSeqs);
  const decodeStep = 0.1 * decodeBatch * Math.sqrt(promptTokens / 100);
  const gaps = COMPLETION_TOKENS - 1;
  const meanRemainingPrefills = (decodeBatch - 1) / 2;
  const duration = concurrentRequests * prefill + gaps * decodeStep;

  return {
    max_num_seqs: maxNumSeqs,
    series_id: `${promptTokens} tokens`,
    concurrent_requests: concurrentRequests,
    median_prompt_tokens: promptTokens,
    completion_tokens: COMPLETION_TOKENS,
    duration,
    median_ttft: ((concurrentRequests + 1) / 2) * prefill,
    average_itl: decodeStep + (meanRemainingPrefills * prefill) / gaps,
    system_throughput: (concurrentRequests * COMPLETION_TOKENS) / duration,
  };
}

const POINTS = [100, 400].flatMap((promptTokens) =>
  [1, 2, 4].map((concurrentRequests) => benchmarkPoint(promptTokens, concurrentRequests)),
);

function estimate(overrides: Partial<JobInputs> = {}) {
  return estimateJob(POINTS, {
    maxNumSeqs: 4,
    concurrentRequests: 4,
    promptTokens: 100,
    completionTokens: COMPLETION_TOKENS,
    ...overrides,
  });
}

describe("estimateJob", () => {
  it("reconstructs an exact measured benchmark cell", () => {
    const result = estimate();

    expect(result).not.toBeNull();
    expect(result?.prefillSeconds).toBeCloseTo(4);
    expect(result?.decodeSeconds).toBeCloseTo(0.8);
    expect(result?.durationSeconds).toBeCloseTo(4.8);
    expect(result?.feltTtft).toBeCloseTo(2.5);
    expect(result?.feltItl).toBeCloseTo(1.15);
    expect(result?.waves).toBe(1);
    expect(result?.extrapolated).toBe(false);
  });

  it("interpolates between measured prompt lengths in log space", () => {
    const result = estimate({ concurrentRequests: 2, promptTokens: 200 });

    expect(result?.prefillPerRequest).toBeCloseTo(2);
    expect(result?.decodeSeconds).toBeCloseTo(0.4 * Math.SQRT2);
    expect(result?.durationSeconds).toBeCloseTo(4 + 0.4 * Math.SQRT2);
    expect(result?.extrapolated).toBe(false);
  });

  it("charges each admission wave at its own decode batch size", () => {
    const result = estimate({ maxNumSeqs: 2, concurrentRequests: 5 });

    expect(result?.waves).toBe(3);
    expect(result?.decodeBatch).toBe(2);
    expect(result?.prefillSeconds).toBeCloseTo(5);
    expect(result?.decodeSeconds).toBeCloseTo(1);
    expect(result?.durationSeconds).toBeCloseTo(6);
    expect(result?.feltTtft).toBeCloseTo(3.4);
    expect(result?.feltItl).toBeCloseTo(0.38);
    expect(result?.extrapolated).toBe(false);
  });

  it.each([
    ["below the measured prompt range", { promptTokens: 50 }],
    ["above the measured prompt range", { promptTokens: 800 }],
    ["outside the measured decode batches", { maxNumSeqs: 8, concurrentRequests: 8 }],
  ])("flags estimates %s", (_description, overrides) => {
    expect(estimate(overrides)?.extrapolated).toBe(true);
  });

  it("flags a prompt curve containing only one measured length", () => {
    const onePromptLength = POINTS.filter((point) => point.median_prompt_tokens === 100);

    const result = estimateJob(onePromptLength, {
      maxNumSeqs: 4,
      concurrentRequests: 2,
      promptTokens: 100,
      completionTokens: COMPLETION_TOKENS,
    });

    expect(result?.extrapolated).toBe(true);
  });

  it("returns null when completion timing is unavailable", () => {
    const withoutCompletionTiming = POINTS.map((point) => ({
      ...point,
      completion_tokens: undefined,
    }));

    expect(
      estimateJob(withoutCompletionTiming, {
        maxNumSeqs: 4,
        concurrentRequests: 4,
        promptTokens: 100,
        completionTokens: COMPLETION_TOKENS,
      }),
    ).toBeNull();
  });
});

describe("measured-cell helpers", () => {
  it("finds the nearest comparable cell and recomputes the model there", () => {
    const cell = nearestMeasuredCell(POINTS, {
      maxNumSeqs: 4,
      concurrentRequests: 3,
      promptTokens: 350,
      completionTokens: COMPLETION_TOKENS,
    });

    expect(cell).toEqual(
      expect.objectContaining({
        concurrentRequests: 4,
        promptTokens: 400,
        completionTokens: COMPLETION_TOKENS,
      }),
    );
    expect(cell?.measuredSeconds).toBeCloseTo(17.6);
    expect(cell?.modelSeconds).toBeCloseTo(17.6);
  });

  it("returns sorted unique server batch sizes and the sweep completion length", () => {
    const mixed = [
      { ...POINTS[0], max_num_seqs: 16 },
      ...POINTS,
      { ...POINTS[1], max_num_seqs: 16 },
    ];

    expect(measuredMaxNumSeqsValues(mixed)).toEqual([4, 16]);
    expect(sweepCompletionTokens(mixed)).toBe(COMPLETION_TOKENS);
  });
});
