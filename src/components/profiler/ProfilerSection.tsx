import { useProfilerDefaults } from "../../lib/queries";
import { ProfilerSetupForm, type ProfilerDockerState } from "./ProfilerSetupForm";
import { ProfilerWorkflow } from "./ProfilerWorkflow";
import { useProfilerController } from "./useProfilerController";

export function ProfilerSection({
  apiKey,
  port,
  docker,
  disabled = false,
  disabledReason,
}: {
  apiKey: string;
  port: number;
  docker: ProfilerDockerState;
  disabled?: boolean;
  disabledReason?: string;
}) {
  const controller = useProfilerController({ apiKey, port });
  const { data: defaults, error: defaultsError } = useProfilerDefaults();

  return (
    <div className="space-y-4">
      {defaults ? (
        <ProfilerSetupForm
          controller={controller}
          defaults={defaults}
          disabled={disabled}
          disabledReason={disabledReason}
          docker={docker}
        />
      ) : defaultsError ? (
        <p className="text-sm text-destructive">Could not load profiler defaults.</p>
      ) : (
        <div className="h-10 animate-pulse rounded-md border bg-muted" />
      )}
      <ProfilerWorkflow controller={controller} />
    </div>
  );
}
