import { useQuery } from "@tanstack/react-query";
import { apiFetch, readJson } from "./api";
import type {
  ConfigStatus,
  DeployStatusResponse,
  ModelsResponse,
  ProfilerArtifactSummary,
  ProfilerJobSnapshot,
  UpstreamStatus,
} from "../types";

export const CONFIG_QUERY_KEY = ["config"];
export const MODELS_QUERY_KEY = ["models"];
export const ACTIVE_PROFILER_JOB_QUERY_KEY = ["profiler-active-job"];
export const PROFILER_ARTIFACTS_QUERY_KEY = ["profiler-artifacts"];
export const UPSTREAM_STATUS_QUERY_KEY = ["upstream-status"];
export const DEPLOY_STATUS_QUERY_KEY = ["deploy-status"];

const JOB_POLL_INTERVAL_MS = 2000;
const UPSTREAM_STATUS_POLL_INTERVAL_MS = 10_000;
const DEPLOY_POLL_INTERVAL_MS = 3000;
const MODELS_POLL_INTERVAL_MS = 5000;

export function isJobActive(job: ProfilerJobSnapshot | null | undefined): boolean {
  return job != null && (job.status === "queued" || job.status === "running");
}

export function useAppConfig() {
  return useQuery({
    queryKey: CONFIG_QUERY_KEY,
    queryFn: async ({ signal }) => readJson<ConfigStatus>(await apiFetch("/api/config", { signal })),
    staleTime: Infinity,
  });
}

export function useModels() {
  return useQuery({
    queryKey: MODELS_QUERY_KEY,
    queryFn: async ({ signal }) => readJson<ModelsResponse>(await apiFetch("/api/models", { signal })),
    staleTime: 60_000,
    // A model only becomes listable once a deployment is up, and nothing pushes that transition to
    // this query (visited tabs stay mounted, so there is no remount to trigger a refetch). Poll while
    // the list is empty so a freshly deployed model appears without a page reload; stop once we have one.
    refetchInterval: (query) => ((query.state.data?.models.length ?? 0) > 0 ? false : MODELS_POLL_INTERVAL_MS),
  });
}

export function useActiveProfilerJob() {
  return useQuery({
    queryKey: ACTIVE_PROFILER_JOB_QUERY_KEY,
    queryFn: async ({ signal }) =>
      readJson<ProfilerJobSnapshot | null>(await apiFetch("/api/profiler/jobs/active", { signal })),
    refetchInterval: (query) => (isJobActive(query.state.data) ? JOB_POLL_INTERVAL_MS : false),
  });
}

export function useUpstreamStatus() {
  return useQuery({
    queryKey: UPSTREAM_STATUS_QUERY_KEY,
    queryFn: async ({ signal }) => readJson<UpstreamStatus>(await apiFetch("/api/status", { signal })),
    refetchInterval: UPSTREAM_STATUS_POLL_INTERVAL_MS,
  });
}

export function useDeployStatus() {
  return useQuery({
    queryKey: DEPLOY_STATUS_QUERY_KEY,
    queryFn: async ({ signal }) =>
      readJson<DeployStatusResponse>(await apiFetch("/api/deploy/status", { signal })),
    refetchInterval: DEPLOY_POLL_INTERVAL_MS,
  });
}

export function useProfilerArtifacts() {
  return useQuery({
    queryKey: PROFILER_ARTIFACTS_QUERY_KEY,
    queryFn: async ({ signal }) =>
      readJson<ProfilerArtifactSummary[]>(await apiFetch("/api/profiler/artifacts", { signal })),
  });
}
