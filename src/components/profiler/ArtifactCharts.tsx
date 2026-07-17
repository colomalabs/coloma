import { useQuery } from "@tanstack/react-query";
import { ScrollText } from "lucide-react";
import { apiFetch, readJson } from "../../lib/api";
import type { BenchPoint, ContextLengthWarning, ProfilerArtifact } from "../../types";
import { JobTimeEstimator } from "../JobTimeEstimator";
import { BenchCharts } from "../BenchCharts";
import { ContextLengthWarnings } from "./ContextLengthWarnings";

function ProfileLogsCard({ warnings }: { warnings: ContextLengthWarning[] }) {
  if (warnings.length === 0) return null;
  return (
    <div className="space-y-1 rounded-md border bg-card px-4 py-3">
      <div className="flex items-center gap-2 text-sm font-medium">
        <ScrollText className="h-4 w-4 text-muted-foreground" />
        Logs
      </div>
      <ContextLengthWarnings warnings={warnings} />
    </div>
  );
}

export function ArtifactCharts({ artifactId }: { artifactId: number }) {
  const { data, isPending, error } = useQuery({
    queryKey: ["profiler-artifact", artifactId],
    queryFn: async ({ signal }) =>
      readJson<ProfilerArtifact>(await apiFetch(`/api/profiler/artifacts/${artifactId}`, { signal })),
  });

  if (isPending) return <div className="h-40 animate-pulse rounded-md border bg-muted" />;
  if (error || !data) {
    return (
      <p className="text-sm text-destructive">
        {error instanceof Error ? error.message : "Could not load profile"}
      </p>
    );
  }

  const points: BenchPoint[] = data.profiling_results.bench_points ?? [];
  return (
    <div className="space-y-3">
      <ProfileLogsCard warnings={data.profiling_results.context_length_warnings ?? []} />
      <BenchCharts
        points={points}
        running={false}
        selectedMaxModelLen={data.profiling_results.selected_max_model_len ?? null}
        stressTests={data.profiling_results.stress_tests ?? []}
      />
      {points.length > 0 ? <JobTimeEstimator points={points} /> : null}
    </div>
  );
}
