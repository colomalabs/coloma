import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, readJson } from "../../lib/api";
import {
  DEPLOY_STATUS_QUERY_KEY,
  PROFILER_ARTIFACTS_QUERY_KEY,
  useProfilerArtifacts,
} from "../../lib/queries";
import type {
  DeployRuntimeStatus,
  DeployStatusResponse,
  ProfilerArtifact,
  ProfilerJobSnapshot,
} from "../../types";

function formatUptime(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

export function useDeploymentController({
  runtime,
  profilerJob,
}: {
  runtime: DeployRuntimeStatus;
  profilerJob: ProfilerJobSnapshot | null | undefined;
}) {
  const queryClient = useQueryClient();
  const previousJobRef = useRef<{ id: string; status: ProfilerJobSnapshot["status"] } | null>(null);
  const [selectedProfileId, setSelectedProfileId] = useState<number | null>(null);
  const [isEditingCommand, setIsEditingCommand] = useState(false);
  const [editedCommand, setEditedCommand] = useState("");

  const runtimeActive = runtime.state === "starting" || runtime.state === "serving" || runtime.state === "stopping";

  const {
    data: profiles = [],
    isPending: profilesPending,
    error: profilesQueryError,
  } = useProfilerArtifacts();

  const { data: selectedArtifact, isPending: artifactPending } = useQuery({
    enabled: selectedProfileId !== null,
    queryKey: ["profiler-artifact", selectedProfileId],
    queryFn: async ({ signal }) =>
      readJson<ProfilerArtifact>(await apiFetch(`/api/profiler/artifacts/${selectedProfileId}`, { signal })),
  });

  const { data: commandPreview, isPending: commandPending } = useQuery({
    enabled: selectedProfileId !== null && !runtimeActive,
    queryKey: ["deploy-command-preview", selectedProfileId],
    queryFn: async ({ signal }) =>
      readJson<{ command: string }>(
        await apiFetch(`/api/deploy/runtime/command?artifact_id=${selectedProfileId}`, { signal }),
      ),
    retry: false,
  });

  // Start and stop both answer with the runtime state they just moved the server to. Publish it
  // straight away: the mutation settles a whole status round-trip before the poll catches up, and
  // without this the controls fall back to the pre-mutation state in between.
  const publishRuntime = (runtime: DeployRuntimeStatus) => {
    queryClient.setQueryData<DeployStatusResponse>(DEPLOY_STATUS_QUERY_KEY, (previous) =>
      previous ? { ...previous, runtime } : previous,
    );
    void queryClient.invalidateQueries({ queryKey: DEPLOY_STATUS_QUERY_KEY });
  };

  const startMutation = useMutation({
    mutationFn: async ({ artifactId, command }: { artifactId: number; command?: string }) =>
      readJson<DeployRuntimeStatus>(
        await apiFetch("/api/deploy/runtime/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ artifact_id: artifactId, command }),
        }),
      ),
    onSuccess: publishRuntime,
  });

  const stopMutation = useMutation({
    mutationFn: async () =>
      readJson<DeployRuntimeStatus>(await apiFetch("/api/deploy/runtime/stop", { method: "POST" })),
    onSuccess: publishRuntime,
  });

  const deleteMutation = useMutation({
    mutationFn: async (artifactId: number) =>
      readJson<{ deleted: number }>(
        await apiFetch(`/api/profiler/artifacts/${artifactId}`, { method: "DELETE" }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: PROFILER_ARTIFACTS_QUERY_KEY });
      setSelectedProfileId(null);
    },
  });

  // The profile behind a running deployment is immutable. Reflect it in the selector after reload
  // and prevent the controls from describing a different profile than the one actually serving.
  useEffect(() => {
    if (runtimeActive && runtime.artifact_id !== null) {
      setSelectedProfileId(runtime.artifact_id);
    }
  }, [runtimeActive, runtime.artifact_id]);

  // A job completion creates a new profile outside the profile-list query. Refresh and select it
  // only when a job observed by this mounted tab transitions to done.
  useEffect(() => {
    const previousJob = previousJobRef.current;
    if (
      profilerJob?.status === "done" &&
      profilerJob.artifact_id !== null &&
      previousJob?.id === profilerJob.id &&
      previousJob.status !== "done"
    ) {
      void queryClient.invalidateQueries({ queryKey: PROFILER_ARTIFACTS_QUERY_KEY });
      setSelectedProfileId(profilerJob.artifact_id);
    }
    previousJobRef.current = profilerJob ? { id: profilerJob.id, status: profilerJob.status } : null;
  }, [profilerJob, queryClient]);

  const command = runtimeActive ? runtime.command : commandPreview?.command ?? "";

  useEffect(() => {
    setIsEditingCommand(false);
  }, [command, runtimeActive]);

  useEffect(() => {
    startMutation.reset();
    stopMutation.reset();
    // Mutation reset functions are stable; including the mutation objects would reset on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedProfileId]);

  const effectiveCommand = isEditingCommand ? editedCommand : command;
  const profilerRunning = profilerJob?.status === "queued" || profilerJob?.status === "running";
  const canDeploy = !!selectedArtifact && !runtimeActive && !profilerRunning && !startMutation.isPending;
  const selectionLocked = runtimeActive || startMutation.isPending || deleteMutation.isPending;
  const selectedArtifactPending =
    selectedProfileId !== null && (artifactPending || (!runtimeActive && commandPending));

  const deploymentStatus =
    startMutation.isPending || runtime.state === "starting"
      ? "Deploying..."
      : runtime.state === "serving"
        ? `Ready · ${formatUptime(runtime.uptime_seconds)}`
        : runtime.state === "stopping"
          ? "Stopping..."
          : runtime.state === "error"
            ? "Deployment failed"
            : "Not deployed";

  const profilesError = profilesQueryError
    ? errorMessage(profilesQueryError, "Could not load saved profiles")
    : "";
  const deploymentError = startMutation.error
    ? errorMessage(startMutation.error, "Could not launch")
    : stopMutation.error
      ? errorMessage(stopMutation.error, "Could not stop")
      : runtime.state === "error"
        ? runtime.error
        : "";

  const kvTokenSize =
    selectedArtifact?.profiling_results.kv_token_size ??
    selectedArtifact?.profiling_results.max_batch_size ??
    null;

  return {
    canDeploy,
    command: effectiveCommand,
    deletePending: deleteMutation.isPending,
    deploymentError,
    deploymentStatus,
    editedCommand,
    isEditingCommand,
    kvTokenSize,
    profiles,
    profilesError,
    profilesPending,
    profilerRunning,
    runtimeActive,
    selectedArtifact,
    selectedArtifactPending,
    selectedProfileId,
    selectionLocked,
    startPending: startMutation.isPending,
    stopDisabled: stopMutation.isPending || runtime.state === "stopping",
    stopPending: stopMutation.isPending,
    selectProfile: setSelectedProfileId,
    setEditedCommand,
    toggleCommandEditing: () => {
      if (isEditingCommand) {
        setIsEditingCommand(false);
      } else {
        setEditedCommand(command);
        setIsEditingCommand(true);
      }
    },
    deploy: () => {
      if (!selectedArtifact || !canDeploy) return;
      startMutation.mutate({
        artifactId: selectedArtifact.id,
        command: isEditingCommand ? editedCommand : undefined,
      });
    },
    stop: () => stopMutation.mutate(),
    deleteProfile: () => {
      if (selectedArtifact && !runtimeActive) {
        deleteMutation.mutate(selectedArtifact.id);
      }
    },
  };
}

export type DeploymentController = ReturnType<typeof useDeploymentController>;
