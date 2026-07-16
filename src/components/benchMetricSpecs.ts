import { type BenchPoint } from "../types";
import { formatSeconds } from "./charts/chartFormatters";

export type BenchMetricSpec = {
  id: string;
  title: string;
  description: string;
  unit: string;
  metric: (point: BenchPoint) => number;
  format: (value: number) => string;
  hint?: (point: BenchPoint) => string;
};

// The two metrics every sweep chart plots. Shared with the compare panel, which draws them
// against concurrency for two hand-picked series instead of prompt lengths of one server.
export const SPECS: BenchMetricSpec[] = [
  {
    id: "median_ttft",
    title: "Median TTFT (s)",
    description:
      "Time To First Token: time a user feels before receiving their first generated token, including queueing " +
      "behind other requests.",
    unit: "s",
    metric: (point) => point.median_ttft,
    format: formatSeconds,
  },
  {
    id: "average_itl",
    title: "ITL (s)",
    description: "Inter-Token Latency: the average time gap between consecutive tokens a user feels.",
    unit: "s",
    metric: (point) => point.average_itl,
    format: formatSeconds,
    hint: (point) => (point.median_itl != null ? `steady ${formatSeconds(point.median_itl)}s` : ""),
  },
];

export function specHint<T>(
  hint: ((point: BenchPoint) => string) | undefined,
  pick: (row: T) => BenchPoint | undefined,
): ((row: T) => string) | undefined {
  if (!hint) return undefined;
  return (row) => {
    const point = pick(row);
    return point ? hint(point) : "";
  };
}
