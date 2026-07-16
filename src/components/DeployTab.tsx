import { useActiveProfilerJob, useAppConfig, useDeployStatus } from "../lib/queries";
import type { DeployRuntimeStatus } from "../types";
import { DeploymentPanel } from "./deploy/DeploymentPanel";
import { useDeploymentController } from "./deploy/useDeploymentController";
import { ArtifactCharts } from "./profiler/ArtifactCharts";
import { ProfilerSection } from "./profiler/ProfilerSection";

const DEFAULT_DEPLOY_RUNTIME: DeployRuntimeStatus = {
  state: "idle",
  artifact_id: null,
  model_name: "",
  command: "",
  started_at: "",
  uptime_seconds: 0,
  container_name: "coloma-deploy",
  gpu_busy: false,
  error: "",
};

export function DeployTab() {
  const { data, isPending, error } = useDeployStatus();
  const { data: configData } = useAppConfig();
  const { data: activeProfilerJob } = useActiveProfilerJob();

  const runtime = data?.runtime ?? DEFAULT_DEPLOY_RUNTIME;
  const deployment = useDeploymentController({ runtime, profilerJob: activeProfilerJob });
  const statusError = error instanceof Error ? error.message : error ? "Could not load deploy status" : "";

  return (
    <section className="mx-auto w-full max-w-[1600px] px-5 py-5">
      <div className="space-y-6">
        {statusError ? <p className="text-sm text-destructive">{statusError}</p> : null}

        <div className="grid gap-6 lg:grid-cols-2">
          <div className="space-y-3">
            <h2 className="text-base font-semibold">Profile</h2>
            <ProfilerSection
              apiKey={configData?.app_config.deployment.api_key ?? ""}
              disabled={deployment.runtimeActive}
              disabledReason="Stop the running deployment to start a new profiling run."
              docker={{
                error: data?.docker_error ?? "",
                images: data?.docker_images ?? [],
                isPending,
                pullStatus: data?.docker_pull ?? { image: "", state: "idle", error: "" },
                targetImage: data?.default_vllm_image ?? "",
              }}
              port={configData?.app_config.deployment.port ?? 8000}
            />
          </div>

          <div className="space-y-3">
            <h2 className="text-base font-semibold">Deploy</h2>
            <DeploymentPanel controller={deployment} />
            {deployment.selectedProfileId !== null ? (
              <ArtifactCharts artifactId={deployment.selectedProfileId} />
            ) : null}
          </div>
        </div>
      </div>
    </section>
  );
}
