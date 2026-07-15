import { useMemo, useState } from "react";
import { LineChart as LineChartIcon } from "lucide-react";
import { formatSeconds, formatTokens, MetricInfo, SeriesChart } from "./charts/SeriesChart";
import { BenchPoint, StressTestResult } from "../types";

// Fixed categorical order (never cycled/reassigned) so a series' color stays tied to its rank
// among the known series_ids (not the raw id, which is an arbitrary n_tokens value and collides
// mod palette length). A 9th concurrent series would need a different encoding (small multiples,
// etc.) rather than an extra generated hue.
const SERIES_PALETTE = ["#2a78d6", "#1baf7a", "#eda100", "#008300", "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"];

function seriesColor(rank: number): string {
  return SERIES_PALETTE[rank % SERIES_PALETTE.length];
}

// One row per concurrency value, holding each series' point for that concurrency so the chart can
// plot every series against a shared x-axis.
type BenchRow = {
  concurrent_requests: number;
  bySeries: Record<string, BenchPoint>;
};

function pivotBySeries(points: BenchPoint[]): { rows: BenchRow[]; seriesIds: string[] } {
  const rows = new Map<number, BenchRow>();
  // Order of first appearance, not sorted alphabetically — series_id is a free-form label
  // (e.g. "1024 tokens") and the backend already emits series in a sensible sweep order.
  const seriesIds: string[] = [];
  for (const point of points) {
    if (!seriesIds.includes(point.series_id)) seriesIds.push(point.series_id);
    let row = rows.get(point.concurrent_requests);
    if (!row) {
      row = { concurrent_requests: point.concurrent_requests, bySeries: {} };
      rows.set(point.concurrent_requests, row);
    }
    row.bySeries[point.series_id] = point;
  }
  return {
    rows: [...rows.values()].sort((a, b) => a.concurrent_requests - b.concurrent_requests),
    seriesIds,
  };
}

// The sweep runs once per server, each booted at its own --max-num-seqs, so the points arrive
// interleaved with the group they belong to. Descending by --max-num-seqs puts the most recently
// completed sweep directly below the progress bar. Legacy artifacts carry no max_num_seqs and
// collapse into one unlabelled group.
type BenchGroup = {
  maxNumSeqs: number | null;
  points: BenchPoint[];
};

function groupByMaxNumSeqs(points: BenchPoint[]): BenchGroup[] {
  const groups = new Map<number | null, BenchPoint[]>();
  for (const point of points) {
    const key = point.max_num_seqs ?? null;
    const group = groups.get(key);
    if (group) group.push(point);
    else groups.set(key, [point]);
  }
  return [...groups.entries()]
    .map(([maxNumSeqs, groupPoints]) => ({ maxNumSeqs, points: groupPoints }))
    .sort((a, b) => (b.maxNumSeqs ?? 0) - (a.maxNumSeqs ?? 0));
}

// Colors are assigned from the series' rank across every group, not within one, so a prompt length
// keeps its color even when a server settled on a shorter context length and dropped the last series.
function allSeriesIds(points: BenchPoint[]): string[] {
  const seriesIds: string[] = [];
  for (const point of points) {
    if (!seriesIds.includes(point.series_id)) seriesIds.push(point.series_id);
  }
  return seriesIds;
}

const MAX_NUM_SEQS_HELP =
  "The maximum number of sequences vLLM can process in one iteration. Higher values permit larger batches, " +
  "which may improve total throughput at the cost of higher per-request latency and KV-cache pressure.";
const MAX_MODEL_LEN_HELP =
  "The maximum combined prompt and completion length accepted by the server. Longer contexts require more " +
  "KV cache per request and can reduce the number of requests served concurrently.";

// The four metrics every sweep chart plots. Shared with the compare panel, which draws the same four
// against two hand-picked series instead of the prompt lengths of one server.
export const SPECS = [
  {
    id: "average_itl",
    title: "Average ITL (s)",
    description:
      "Inter-token latency: the average gap between two consecutive tokens of one response. This is what streaming speed feels like to a single user.",
    unit: "s",
    metric: (p: BenchPoint) => p.average_itl,
    format: formatSeconds,
  },
  {
    id: "system_throughput",
    title: "System throughput (tokens/s)",
    description:
      "Tokens generated per second by the whole system, counting the time spent on prefill.",
    unit: " tok/s",
    metric: (p: BenchPoint) => p.system_throughput,
    format: formatTokens,
  },
  {
    id: "median_ttft",
    title: "Median TTFT (s)",
    description:
      "Time To First Token: how long a request waits before its first token comes back.",
    unit: "s",
    metric: (p: BenchPoint) => p.median_ttft,
    format: formatSeconds,
  },
  // NaN (not 0) on artifacts profiled before this metric existed, so the line breaks instead of
  // plotting a fake zero.
  {
    id: "system_decoding_throughput",
    title: "System decoding throughput (tokens/s)",
    description:
      "Tokens per second the server emits while decoding, added up across the concurrent requests and excluding prefill.",
    unit: " tok/s",
    metric: (p: BenchPoint) => p.system_decoding_throughput ?? NaN,
    format: formatTokens,
  },
];

// One card per server: its own header, its own legend, and its own log-scale toggle — the three
// servers are read one at a time, and a scale that suits one need not suit the next.
function BenchGroupSection({
  group,
  colorOf,
  stressTests,
  selectedMaxModelLen,
}: {
  group: BenchGroup;
  colorOf: (seriesId: string) => string;
  stressTests: StressTestResult[];
  selectedMaxModelLen?: number | null;
}) {
  const [logScale, setLogScale] = useState(false);
  const { rows, seriesIds } = useMemo(() => pivotBySeries(group.points), [group.points]);
  const stressTest = stressTests.find((result) => result.max_num_seqs === group.maxNumSeqs);
  const serverMaxModelLen = stressTest?.max_model_len ?? selectedMaxModelLen;

  return (
    <section className="rounded-md border bg-card p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-medium">
          <LineChartIcon className="h-4 w-4 shrink-0" />
          {group.maxNumSeqs == null ? (
            // A legacy artifact, profiled before the sweep ran against several servers: there is no
            // --max-num-seqs to name it by.
            "Performance vs request parallelism"
          ) : (
            <>
              <code className="font-mono">--max-num-seqs = {group.maxNumSeqs}</code>
              <MetricInfo description={MAX_NUM_SEQS_HELP} />
            </>
          )}
          {serverMaxModelLen != null ? (
            <>
              <code className="font-mono">--max-model-len = {serverMaxModelLen.toLocaleString()}</code>
              <MetricInfo description={MAX_MODEL_LEN_HELP} />
            </>
          ) : null}
        </div>
        <div className="ml-auto flex flex-wrap items-center justify-end gap-4 text-xs text-muted-foreground">
          {seriesIds.length > 1 || stressTest
            ? seriesIds.map((id) => (
                <span key={id} className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-full" style={{ background: colorOf(id) }} />
                  {id}
                </span>
              ))
            : null}
          {stressTest ? (
            <span className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full" style={{ background: "#e34948" }} />
              {stressTest.max_model_len - 1} tokens
            </span>
          ) : null}
          <label className="flex cursor-pointer select-none items-center gap-1.5">
            <input checked={logScale} className="accent-primary" onChange={(event) => setLogScale(event.target.checked)} type="checkbox" />
            Log y-axis
          </label>
        </div>
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        {SPECS.map((spec) => (
          <SeriesChart
            description={spec.description}
            format={spec.format}
            key={spec.title}
            logScale={logScale}
            points={rows}
            pointKey={(row) => row.concurrent_requests}
            series={[
              ...seriesIds.map((id) => ({
                label: id,
                color: colorOf(id),
                value: (row: BenchRow) => (row.bySeries[id] ? spec.metric(row.bySeries[id]) : NaN),
              })),
              ...(spec.id === "median_ttft" && stressTest
                ? [{
                    id: "full-context-stress",
                    label: `${stressTest.max_model_len - 1} tokens`,
                    color: "#e34948",
                    value: (row: BenchRow) => (
                      row.concurrent_requests === stressTest.max_num_seqs ? stressTest.median_ttft : NaN
                    ),
                  }]
                : []),
            ]}
            title={spec.title}
            tooltipTitle={(row) => `${row.concurrent_requests} concurrent`}
            unit={spec.unit}
            xAxisLabel="Concurrent requests"
            xTickLabel={(row) => String(row.concurrent_requests)}
            xValue={(row) => row.concurrent_requests}
          />
        ))}
      </div>
    </section>
  );
}

export function BenchCharts({
  points,
  stressTests = [],
  running,
  selectedMaxModelLen,
}: {
  points: BenchPoint[];
  stressTests?: StressTestResult[];
  running: boolean;
  selectedMaxModelLen?: number | null;
}) {
  const groups = useMemo(() => groupByMaxNumSeqs(points), [points]);
  const seriesIds = useMemo(() => allSeriesIds(points), [points]);
  const colorOf = (seriesId: string) => seriesColor(Math.max(0, seriesIds.indexOf(seriesId)));

  if (points.length === 0) {
    return (
      <section className="rounded-md border bg-card p-3">
        <div className="flex items-center gap-2 text-sm font-medium">
          <LineChartIcon className="h-4 w-4" />
          Performance vs request parallelism
        </div>
        <div className="py-8 text-center text-sm text-muted-foreground">
          {running ? "Charts will appear as benchmark points arrive." : "No benchmark data yet."}
        </div>
      </section>
    );
  }

  return (
    <div className="space-y-3">
      {groups.map((group) => (
        <BenchGroupSection
          colorOf={colorOf}
          group={group}
          key={group.maxNumSeqs ?? "all"}
          selectedMaxModelLen={selectedMaxModelLen}
          stressTests={stressTests}
        />
      ))}
    </div>
  );
}
