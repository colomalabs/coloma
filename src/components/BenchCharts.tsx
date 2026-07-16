import { useMemo, useState } from "react";
import { LineChart as LineChartIcon } from "lucide-react";
import { MetricInfo, SeriesChart } from "./charts/SeriesChart";
import { formatTokens } from "./charts/chartFormatters";
import { SPECS } from "./benchMetricSpecs";
import { promptTokensOf } from "../lib/benchPoints";
import { BenchPoint, StressTestResult } from "../types";

// Fixed categorical order (never cycled/reassigned) so a series' color stays tied to its rank
// among the known concurrency values. The default grid has exactly 8 of them; a 9th concurrent
// series would need a different encoding (small multiples, etc.) rather than an extra generated hue.
const SERIES_PALETTE = ["#2a78d6", "#1baf7a", "#eda100", "#008300", "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"];

function seriesColor(rank: number): string {
  return SERIES_PALETTE[rank % SERIES_PALETTE.length];
}

// One row per prompt length, holding each concurrency's point for that length so the chart can plot
// every concurrency series against a shared prompt-token x-axis.
type BenchRow = {
  promptTokens: number;
  label: string;
  byConcurrency: Record<number, BenchPoint>;
  // Set only on the extra row the full-context stress test contributes to the felt-TTFT chart.
  stressTtft?: number;
};

function pivotByPromptLength(points: BenchPoint[]): { rows: BenchRow[]; concurrencies: number[] } {
  const rows = new Map<string, BenchRow>();
  const concurrencies: number[] = [];
  for (const point of points) {
    if (!concurrencies.includes(point.concurrent_requests)) concurrencies.push(point.concurrent_requests);
    let row = rows.get(point.series_id);
    if (!row) {
      row = { promptTokens: promptTokensOf(point), label: point.series_id, byConcurrency: {} };
      rows.set(point.series_id, row);
    }
    row.byConcurrency[point.concurrent_requests] = point;
  }
  return {
    rows: [...rows.values()].sort((a, b) => a.promptTokens - b.promptTokens),
    concurrencies: concurrencies.sort((a, b) => a - b),
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

// Colors are assigned from the concurrency's rank across every group, not within one, so a
// concurrency keeps its color even when one server's sweep timed out before reaching the higher ones.
function allConcurrencyValues(points: BenchPoint[]): number[] {
  const values: number[] = [];
  for (const point of points) {
    if (!values.includes(point.concurrent_requests)) values.push(point.concurrent_requests);
  }
  return values.sort((a, b) => a - b);
}

const MAX_NUM_SEQS_HELP =
  "The maximum number of sequences vLLM can process in one iteration. Higher values permit larger batches, " +
  "which may improve total throughput at the cost of higher per-request latency and KV-cache pressure.";
const MAX_MODEL_LEN_HELP =
  "The maximum combined prompt and completion length accepted by the server. Longer contexts require more " +
  "KV cache per request and can reduce the number of requests served concurrently.";

// One card per server: its own header, its own legend, and its own log-scale toggle — the three
// servers are read one at a time, and a scale that suits one need not suit the next.
function BenchGroupSection({
  group,
  colorOf,
  stressTests,
  selectedMaxModelLen,
}: {
  group: BenchGroup;
  colorOf: (concurrency: number) => string;
  stressTests: StressTestResult[];
  selectedMaxModelLen?: number | null;
}) {
  const [logScale, setLogScale] = useState(false);
  const { rows, concurrencies } = useMemo(() => pivotByPromptLength(group.points), [group.points]);
  const stressTest = stressTests.find((result) => result.max_num_seqs === group.maxNumSeqs);
  const serverMaxModelLen = stressTest?.max_model_len ?? selectedMaxModelLen;

  // The stress test is one more (prompt length, TTFT) measurement — the full context, fired at a
  // concurrency of exactly --max-num-seqs — so it extends the felt-TTFT chart to the context limit.
  const ttftRows = useMemo(() => {
    if (!stressTest) return rows;
    const stressRow: BenchRow = {
      promptTokens: stressTest.max_model_len - 1,
      label: `${stressTest.max_model_len - 1} tokens`,
      byConcurrency: {},
      stressTtft: stressTest.median_ttft,
    };
    return [...rows, stressRow].sort((a, b) => a.promptTokens - b.promptTokens);
  }, [rows, stressTest]);

  return (
    <section className="rounded-md border bg-card p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-medium">
          <LineChartIcon className="h-4 w-4 shrink-0" />
          {group.maxNumSeqs == null ? (
            // A legacy artifact, profiled before the sweep ran against several servers: there is no
            // --max-num-seqs to name it by.
            "Performance vs prompt length"
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
          {concurrencies.length > 1 || stressTest
            ? concurrencies.map((concurrency) => (
                <span key={concurrency} className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-full" style={{ background: colorOf(concurrency) }} />
                  {concurrency} concurrent
                </span>
              ))
            : null}
          {stressTest ? (
            <span className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full" style={{ background: "#e34948" }} />
              full context
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
            points={spec.id === "median_ttft" ? ttftRows : rows}
            pointKey={(row) => row.label}
            series={[
              ...concurrencies.map((concurrency) => ({
                id: String(concurrency),
                label: `${concurrency} concurrent`,
                color: colorOf(concurrency),
                value: (row: BenchRow) => {
                  const point = row.byConcurrency[concurrency];
                  return point ? spec.metric(point) : NaN;
                },
              })),
              ...(spec.id === "median_ttft" && stressTest
                ? [{
                    id: "full-context-stress",
                    label: `full context, ${stressTest.max_num_seqs} concurrent`,
                    color: "#e34948",
                    value: (row: BenchRow) => row.stressTtft ?? NaN,
                  }]
                : []),
            ]}
            title={spec.title}
            tooltipTitle={(row) => row.label}
            unit={spec.unit}
            xAxisLabel="Prompt tokens"
            xScale="linear"
            xTickLabel={(row) => formatTokens(row.promptTokens)}
            xValue={(row) => row.promptTokens}
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
  const concurrencyValues = useMemo(() => allConcurrencyValues(points), [points]);
  const colorOf = (concurrency: number) => seriesColor(Math.max(0, concurrencyValues.indexOf(concurrency)));

  if (points.length === 0) {
    return (
      <section className="rounded-md border bg-card p-3">
        <div className="flex items-center gap-2 text-sm font-medium">
          <LineChartIcon className="h-4 w-4" />
          Performance vs prompt length
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
