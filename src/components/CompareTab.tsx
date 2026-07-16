import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiFetch, readJson } from "../lib/api";
import { useProfilerArtifacts } from "../lib/queries";
import { SPECS, specHint } from "./benchMetricSpecs";
import { SeriesChart } from "./charts/SeriesChart";
import { formatTokens } from "./charts/chartFormatters";
import { BenchPoint, ProfilerArtifact, ProfilerArtifactSummary } from "../types";
import {
  benchPoints,
  compareRows,
  concurrentRequestsOptions,
  EMPTY_PICK,
  maxNumSeqsOptions,
  pickLabel,
  PICK_COLORS,
  resolvePick,
  selectedPoints,
  type CompareRow,
  type Pick,
  type PickId,
} from "./deploy/profileComparison";

function useArtifact(artifactId: number | null) {
  return useQuery({
    // Same key as ArtifactCharts, so picking a profile that is already on screen costs no fetch.
    queryKey: ["profiler-artifact", artifactId],
    enabled: artifactId !== null,
    queryFn: async ({ signal }) =>
      readJson<ProfilerArtifact>(await apiFetch(`/api/profiler/artifacts/${artifactId}`, { signal })),
  });
}

const SELECT_CLASS =
  "h-9 min-w-0 rounded-md border border-input bg-background px-2 text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";

function PickRow({
  pickId,
  pick,
  resolved,
  artifacts,
  points,
  onChange,
}: {
  pickId: PickId;
  pick: Pick;
  resolved: { maxNumSeqs: number | null; concurrentRequests: number | null };
  artifacts: ProfilerArtifactSummary[];
  points: BenchPoint[];
  onChange: (pick: Pick) => void;
}) {
  const seqsOptions = maxNumSeqsOptions(points);
  const concurrencies = concurrentRequestsOptions(points, resolved.maxNumSeqs);
  const noArtifact = pick.artifactId === null;

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span
        className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs font-medium text-white"
        style={{ background: PICK_COLORS[pickId] }}
      >
        {pickId.toUpperCase()}
      </span>
      <select
        aria-label={`Profile ${pickId.toUpperCase()}`}
        className={`${SELECT_CLASS} flex-1`}
        onChange={(event) =>
          // The new profile may not have the selected server/concurrency: clear them and let
          // resolvePick land on this artifact's own first options.
          onChange({ artifactId: Number(event.target.value), maxNumSeqs: null, concurrentRequests: null })
        }
        value={pick.artifactId ?? ""}
      >
        {artifacts.map((artifact) => (
          <option key={artifact.id} value={artifact.id}>
            {artifact.model_name} — {new Date(artifact.created_at).toLocaleString()}
          </option>
        ))}
      </select>
      <select
        aria-label={`--max-num-seqs ${pickId.toUpperCase()}`}
        className={`${SELECT_CLASS} w-36`}
        disabled={noArtifact || seqsOptions.length === 0}
        onChange={(event) =>
          onChange({ ...pick, maxNumSeqs: event.target.value === "" ? null : Number(event.target.value), concurrentRequests: null })
        }
        value={resolved.maxNumSeqs ?? ""}
      >
        {seqsOptions.map((value) => (
          <option key={value ?? "none"} value={value ?? ""}>
            {value === null ? "seqs n/a" : `seqs = ${value}`}
          </option>
        ))}
      </select>
      <select
        aria-label={`Concurrent requests ${pickId.toUpperCase()}`}
        className={`${SELECT_CLASS} w-36`}
        disabled={noArtifact || concurrencies.length === 0}
        onChange={(event) => onChange({ ...pick, concurrentRequests: Number(event.target.value) })}
        value={resolved.concurrentRequests ?? ""}
      >
        {concurrencies.map((concurrency) => (
          <option key={concurrency} value={concurrency}>
            {concurrency} concurrent
          </option>
        ))}
      </select>
    </div>
  );
}

export function CompareTab() {
  const [logScale, setLogScale] = useState(false);
  const [picks, setPicks] = useState<Record<PickId, Pick>>({ a: EMPTY_PICK, b: EMPTY_PICK });

  const { data: artifacts } = useProfilerArtifacts();

  const summaries = artifacts ?? [];
  // Both sides start on the newest profile: the two lines land on top of each other, and the first
  // dropdown the user touches is what makes them differ. Comparing a profile with itself is allowed —
  // that is how you read one server against another within a single run.
  const newestId = summaries[0]?.id ?? null;
  const pickA = picks.a.artifactId === null ? { ...EMPTY_PICK, artifactId: newestId } : picks.a;
  const pickB = picks.b.artifactId === null ? { ...EMPTY_PICK, artifactId: newestId } : picks.b;

  const { data: artifactA } = useArtifact(pickA.artifactId);
  const { data: artifactB } = useArtifact(pickB.artifactId);

  const pointsA = benchPoints(artifactA);
  const pointsB = benchPoints(artifactB);
  const resolvedA = resolvePick(pickA, pointsA);
  const resolvedB = resolvePick(pickB, pointsB);

  const rows = useMemo(
    () =>
      compareRows(
        selectedPoints(pointsA, resolvedA.maxNumSeqs, resolvedA.concurrentRequests),
        selectedPoints(pointsB, resolvedB.maxNumSeqs, resolvedB.concurrentRequests),
      ),
    [pointsA, pointsB, resolvedA.maxNumSeqs, resolvedA.concurrentRequests, resolvedB.maxNumSeqs, resolvedB.concurrentRequests],
  );

  const labelA = pickLabel(artifactA, resolvedA);
  const labelB = pickLabel(artifactB, resolvedB);

  return (
    <section className="mx-auto w-full max-w-[1600px] px-5 py-5">
      <div className="max-w-3xl space-y-5">
        <div className="space-y-1">
          <h2 className="text-base font-semibold">Compare</h2>
          <p className="text-sm text-muted-foreground">
            Two profiles, servers, or prompt lengths, side by side.
          </p>
        </div>

        {summaries.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No saved profiles yet. Run the profiler from the Profile &amp; deploy tab to create one.
          </p>
        ) : (
          <div className="space-y-4">
            <div className="space-y-2">
              <PickRow
                artifacts={summaries}
                onChange={(pick) => setPicks((current) => ({ ...current, a: pick }))}
                pick={pickA}
                pickId="a"
                points={pointsA}
                resolved={resolvedA}
              />
              <PickRow
                artifacts={summaries}
                onChange={(pick) => setPicks((current) => ({ ...current, b: pick }))}
                pick={pickB}
                pickId="b"
                points={pointsB}
                resolved={resolvedB}
              />
            </div>

            <section className="rounded-md border bg-card p-3">
              <div className="mb-2 flex flex-wrap items-center justify-end gap-4 text-xs text-muted-foreground">
                <span className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-full" style={{ background: PICK_COLORS.a }} />
                  {labelA}
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-full" style={{ background: PICK_COLORS.b }} />
                  {labelB}
                </span>
                <label className="flex cursor-pointer select-none items-center gap-1.5">
                  <input checked={logScale} className="accent-primary" onChange={(event) => setLogScale(event.target.checked)} type="checkbox" />
                  Log y-axis
                </label>
              </div>

              {rows.length === 0 ? (
                <p className="py-6 text-center text-sm text-muted-foreground">
                  No benchmark points for this selection.
                </p>
              ) : (
                <div className="grid gap-6 lg:grid-cols-2">
                  {SPECS.map((spec) => (
                    <SeriesChart
                      description={spec.description}
                      format={spec.format}
                      key={spec.title}
                      logScale={logScale}
                      points={rows}
                      pointKey={(row) => row.label}
                      series={[
                        {
                          // Keyed by the pick, not the label: A and B carry the same label whenever they
                          // point at the same profile/server/prompt length, which is the default state.
                          id: "a",
                          label: labelA,
                          color: PICK_COLORS.a,
                          value: (row: CompareRow) => (row.a ? spec.metric(row.a) : NaN),
                          hint: specHint(spec.hint, (row: CompareRow) => row.a),
                        },
                        {
                          id: "b",
                          label: labelB,
                          color: PICK_COLORS.b,
                          value: (row: CompareRow) => (row.b ? spec.metric(row.b) : NaN),
                          hint: specHint(spec.hint, (row: CompareRow) => row.b),
                        },
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
              )}
            </section>
          </div>
        )}
      </div>
    </section>
  );
}
