import { promptTokensOf } from "../../lib/benchPoints";
import type { BenchPoint, ProfilerArtifact } from "../../types";

export const PICK_COLORS = { a: "#2a78d6", b: "#e34948" } as const;
export type PickId = "a" | "b";

// A pick names one line of one profile: a server (--max-num-seqs) at one concurrency. The prompt
// length is not picked — it is the x-axis, matching the benchmark charts.
export type Pick = {
  artifactId: number | null;
  maxNumSeqs: number | null;
  concurrentRequests: number | null;
};

export const EMPTY_PICK: Pick = { artifactId: null, maxNumSeqs: null, concurrentRequests: null };

// One row per prompt length. The two picks may come from different profiles whose grids differ, so
// a row can hold only one side; rows are merged by the series label the sweeps share.
export type CompareRow = {
  promptTokens: number;
  label: string;
  a?: BenchPoint;
  b?: BenchPoint;
};

export function benchPoints(artifact: ProfilerArtifact | undefined): BenchPoint[] {
  return artifact?.profiling_results.bench_points ?? [];
}

export function maxNumSeqsOptions(points: BenchPoint[]): (number | null)[] {
  const values = new Set<number | null>();
  for (const point of points) values.add(point.max_num_seqs ?? null);
  return [...values].sort((a, b) => (a ?? 0) - (b ?? 0));
}

export function concurrentRequestsOptions(points: BenchPoint[], maxNumSeqs: number | null): number[] {
  const values = new Set<number>();
  for (const point of points) {
    if ((point.max_num_seqs ?? null) !== maxNumSeqs) continue;
    values.add(point.concurrent_requests);
  }
  return [...values].sort((a, b) => a - b);
}

export function resolvePick(
  pick: Pick,
  points: BenchPoint[],
): { maxNumSeqs: number | null; concurrentRequests: number | null } {
  const seqsOptions = maxNumSeqsOptions(points);
  const maxNumSeqs =
    seqsOptions.length === 0
      ? null
      : seqsOptions.some((value) => value === pick.maxNumSeqs)
        ? pick.maxNumSeqs
        : seqsOptions[0];
  const concurrencyOptions = concurrentRequestsOptions(points, maxNumSeqs);
  const concurrentRequests =
    concurrencyOptions.length === 0
      ? null
      : concurrencyOptions.includes(pick.concurrentRequests ?? -1)
        ? pick.concurrentRequests
        : concurrencyOptions[0];
  return { maxNumSeqs, concurrentRequests };
}

export function selectedPoints(
  points: BenchPoint[],
  maxNumSeqs: number | null,
  concurrentRequests: number | null,
): BenchPoint[] {
  return points.filter(
    (point) =>
      (point.max_num_seqs ?? null) === maxNumSeqs && point.concurrent_requests === concurrentRequests,
  );
}

export function compareRows(pointsA: BenchPoint[], pointsB: BenchPoint[]): CompareRow[] {
  const rows = new Map<string, CompareRow>();
  const put = (point: BenchPoint, pickId: PickId) => {
    const row = rows.get(point.series_id) ?? {
      promptTokens: promptTokensOf(point),
      label: point.series_id,
    };
    row[pickId] = point;
    rows.set(point.series_id, row);
  };
  for (const point of pointsA) put(point, "a");
  for (const point of pointsB) put(point, "b");
  return [...rows.values()].sort((a, b) => a.promptTokens - b.promptTokens);
}

export function pickLabel(
  artifact: ProfilerArtifact | undefined,
  resolved: { maxNumSeqs: number | null; concurrentRequests: number | null },
): string {
  if (!artifact) return "—";
  const seqs = resolved.maxNumSeqs === null ? "" : ` · seqs ${resolved.maxNumSeqs}`;
  const concurrent = resolved.concurrentRequests === null ? "" : ` · ${resolved.concurrentRequests} concurrent`;
  return `${artifact.model_name}${seqs}${concurrent}`;
}
