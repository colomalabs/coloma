import type { BenchPoint, ProfilerArtifact } from "../../types";

export const PICK_COLORS = { a: "#2a78d6", b: "#e34948" } as const;
export type PickId = "a" | "b";

export type Pick = {
  artifactId: number | null;
  maxNumSeqs: number | null;
  seriesId: string | null;
};

export const EMPTY_PICK: Pick = { artifactId: null, maxNumSeqs: null, seriesId: null };

export type CompareRow = {
  concurrent_requests: number;
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

export function seriesIdOptions(points: BenchPoint[], maxNumSeqs: number | null): string[] {
  const ids: string[] = [];
  for (const point of points) {
    if ((point.max_num_seqs ?? null) !== maxNumSeqs) continue;
    if (!ids.includes(point.series_id)) ids.push(point.series_id);
  }
  return ids;
}

export function resolvePick(
  pick: Pick,
  points: BenchPoint[],
): { maxNumSeqs: number | null; seriesId: string | null } {
  const seqsOptions = maxNumSeqsOptions(points);
  const maxNumSeqs =
    seqsOptions.length === 0
      ? null
      : seqsOptions.some((value) => value === pick.maxNumSeqs)
        ? pick.maxNumSeqs
        : seqsOptions[0];
  const ids = seriesIdOptions(points, maxNumSeqs);
  const seriesId = ids.length === 0 ? null : ids.includes(pick.seriesId ?? "") ? pick.seriesId : ids[0];
  return { maxNumSeqs, seriesId };
}

export function selectedPoints(
  points: BenchPoint[],
  maxNumSeqs: number | null,
  seriesId: string | null,
): BenchPoint[] {
  return points.filter(
    (point) => (point.max_num_seqs ?? null) === maxNumSeqs && point.series_id === seriesId,
  );
}

export function compareRows(pointsA: BenchPoint[], pointsB: BenchPoint[]): CompareRow[] {
  const rows = new Map<number, CompareRow>();
  const put = (point: BenchPoint, pickId: PickId) => {
    const row = rows.get(point.concurrent_requests) ?? { concurrent_requests: point.concurrent_requests };
    row[pickId] = point;
    rows.set(point.concurrent_requests, row);
  };
  for (const point of pointsA) put(point, "a");
  for (const point of pointsB) put(point, "b");
  return [...rows.values()].sort((a, b) => a.concurrent_requests - b.concurrent_requests);
}

export function pickLabel(
  artifact: ProfilerArtifact | undefined,
  resolved: { maxNumSeqs: number | null; seriesId: string | null },
): string {
  if (!artifact) return "—";
  const seqs = resolved.maxNumSeqs === null ? "" : ` · seqs ${resolved.maxNumSeqs}`;
  const tokens = resolved.seriesId ? ` · ${resolved.seriesId}` : "";
  return `${artifact.model_name}${seqs}${tokens}`;
}
