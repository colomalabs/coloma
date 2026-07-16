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

  return (
    <div className="space-y-4">
      <ProfilerSetupForm
        controller={controller}
        disabled={disabled}
        disabledReason={disabledReason}
        docker={docker}
      />
      <ProfilerWorkflow controller={controller} />
    </div>
  );
}
