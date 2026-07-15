import { useQuery } from "@tanstack/react-query";
import { apiFetch, readJson } from "./api";
import type {
  ConfigStatus,
  DeployStatusResponse,
  ProfilerArtifactSummary,
  ProfilerJobSnapshot,
  UpstreamStatus,
} from "../types";

export const CONFIG_QUERY_KEY = ["config"];
export const ACTIVE_PROFILER_JOB_QUERY_KEY = ["profiler-active-job"];
export const PROFILER_ARTIFACTS_QUERY_KEY = ["profiler-artifacts"];
export const UPSTREAM_STATUS_QUERY_KEY = ["upstream-status"];
export const DEPLOY_STATUS_QUERY_KEY = ["deploy-status"];

const JOB_POLL_INTERVAL_MS = 2000;
const UPSTREAM_STATUS_POLL_INTERVAL_MS = 10_000;
const DEPLOY_POLL_INTERVAL_MS = 3000;

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
