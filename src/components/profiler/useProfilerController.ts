import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch, readJson } from "../../lib/api";
import {
  ACTIVE_PROFILER_JOB_QUERY_KEY,
  isJobActive,
  useActiveProfilerJob,
} from "../../lib/queries";
import type { ProfilerJobSnapshot } from "../../types";

type OomRecoveryVars =
  // "deploy" finalizes the profile with these values; "save_retry" saves them as a profile, then the
  // sweep restarts smaller; "retry" just restarts and carries no values.
  | { jobId: string; action: "deploy" | "save_retry"; maxNumSeqs: number; maxModelLen: number }
  | { jobId: string; action: "retry" };

export type ProfilerStartOptions = {
  modelName: string;
  imageTag: string;
  fp8: boolean;
  extraVllmArgs: string;
  ttftTimeout: number;
  stressTestTimeout: number;
  maxNumSeqsValues: number[];
  concurrentRequestValues: number[];
};

export function useProfilerController({ apiKey, port }: { apiKey: string; port: number }) {
  const queryClient = useQueryClient();
  const { data: job } = useActiveProfilerJob();
  const [startError, setStartError] = useState("");
  const [chooseError, setChooseError] = useState("");
  const [skipError, setSkipError] = useState("");
  const [oomRecoveryError, setOomRecoveryError] = useState("");

  const syncJob = (snapshot: ProfilerJobSnapshot) => {
    queryClient.setQueryData(ACTIVE_PROFILER_JOB_QUERY_KEY, snapshot);
    void queryClient.invalidateQueries({ queryKey: ACTIVE_PROFILER_JOB_QUERY_KEY });
  };

  const startMutation = useMutation({
    mutationFn: async (options: ProfilerStartOptions) =>
      readJson<ProfilerJobSnapshot>(
        await apiFetch("/api/profiler/jobs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            model_name: options.modelName,
            ...(apiKey ? { api_key: apiKey } : {}),
            port,
            image_tag: options.imageTag,
            fp8: options.fp8,
            extra_vllm_args: options.extraVllmArgs.trim(),
            ttft_timeout: options.ttftTimeout,
            stress_test_timeout: options.stressTestTimeout,
            max_num_seqs_values: options.maxNumSeqsValues,
            concurrent_request_values: options.concurrentRequestValues,
          }),
        }),
      ),
    onSuccess: (snapshot) => {
      setStartError("");
      syncJob(snapshot);
    },
    onError: (error) => setStartError(error instanceof Error ? error.message : "Could not start profiler"),
  });

  const cancelMutation = useMutation({
    mutationFn: async (jobId: string) =>
      readJson<ProfilerJobSnapshot>(
        await apiFetch(`/api/profiler/jobs/${jobId}/cancel`, { method: "POST" }),
      ),
    onSuccess: () => queryClient.setQueryData(ACTIVE_PROFILER_JOB_QUERY_KEY, null),
  });

  const skipMutation = useMutation({
    mutationFn: async (jobId: string) =>
      readJson<ProfilerJobSnapshot>(
        await apiFetch(`/api/profiler/jobs/${jobId}/skip-benchmark`, { method: "POST" }),
      ),
    onSuccess: (snapshot) => {
      setSkipError("");
      syncJob(snapshot);
    },
    onError: (error) =>
      setSkipError(error instanceof Error ? error.message : "Could not skip the benchmark"),
  });

  const chooseMutation = useMutation({
    mutationFn: async ({
      jobId,
      maxNumSeqs,
      maxModelLen,
    }: {
      jobId: string;
      maxNumSeqs: number;
      maxModelLen: number;
    }) =>
      readJson<ProfilerJobSnapshot>(
        await apiFetch(`/api/profiler/jobs/${jobId}/choose-deploy`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ max_num_seqs: maxNumSeqs, max_model_len: maxModelLen }),
        }),
      ),
    onSuccess: (snapshot) => {
      setChooseError("");
      syncJob(snapshot);
    },
    onError: (error) =>
      setChooseError(error instanceof Error ? error.message : "Could not set the deploy configuration"),
  });

  const oomRecoveryMutation = useMutation({
    mutationFn: async (vars: OomRecoveryVars) =>
      readJson<ProfilerJobSnapshot>(
        await apiFetch(`/api/profiler/jobs/${vars.jobId}/oom-recovery`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(
            vars.action === "retry"
              ? { action: "retry" }
              : { action: vars.action, max_num_seqs: vars.maxNumSeqs, max_model_len: vars.maxModelLen },
          ),
        }),
      ),
    onSuccess: (snapshot) => {
      setOomRecoveryError("");
      syncJob(snapshot);
    },
    onError: (error) =>
      setOomRecoveryError(error instanceof Error ? error.message : "Could not recover from CUDA out of memory"),
  });

  useEffect(() => {
    setChooseError("");
    setSkipError("");
    setOomRecoveryError("");
  }, [job?.id]);

  return {
    job,
    running: isJobActive(job),
    startError,
    chooseError,
    skipError,
    oomRecoveryError,
    startPending: startMutation.isPending,
    cancelPending: cancelMutation.isPending,
    choosePending: chooseMutation.isPending,
    skipPending: skipMutation.isPending,
    oomRecoveryPending: oomRecoveryMutation.isPending,
    // Which recovery button is in flight, so only that one shows a spinner.
    oomRecoveryPendingAction: oomRecoveryMutation.isPending
      ? oomRecoveryMutation.variables?.action ?? null
      : null,
    start: (options: ProfilerStartOptions) => {
      setStartError("");
      startMutation.mutate(options);
    },
    cancel: () => {
      if (job && isJobActive(job)) cancelMutation.mutate(job.id);
    },
    skipBenchmark: () => {
      if (!job) return;
      setSkipError("");
      skipMutation.mutate(job.id);
    },
    chooseDeployConfig: (maxNumSeqs: number, maxModelLen: number) => {
      if (!job) return;
      setChooseError("");
      chooseMutation.mutate({ jobId: job.id, maxNumSeqs, maxModelLen });
    },
    deployOomRecovery: (maxNumSeqs: number, maxModelLen: number) => {
      if (!job) return;
      setOomRecoveryError("");
      oomRecoveryMutation.mutate({ jobId: job.id, action: "deploy", maxNumSeqs, maxModelLen });
    },
    saveAndRetryOomRecovery: (maxNumSeqs: number, maxModelLen: number) => {
      if (!job) return;
      setOomRecoveryError("");
      oomRecoveryMutation.mutate({ jobId: job.id, action: "save_retry", maxNumSeqs, maxModelLen });
    },
    retryOomRecovery: () => {
      if (!job) return;
      setOomRecoveryError("");
      oomRecoveryMutation.mutate({ jobId: job.id, action: "retry" });
    },
  };
}

export type ProfilerController = ReturnType<typeof useProfilerController>;
