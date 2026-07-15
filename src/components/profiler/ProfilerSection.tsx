import { ProfilerSetupForm, type ProfilerDockerState } from "./ProfilerSetupForm";
import { ProfilerWorkflow } from "./ProfilerWorkflow";
import { useProfilerController } from "./useProfilerController";

export function ProfilerSection({
  apiKey,
  port,
  docker,
  disabled = false,
}: {
  apiKey: string;
  port: number;
  docker: ProfilerDockerState;
  disabled?: boolean;
}) {
  const controller = useProfilerController({ apiKey, port });

  return (
    <div className="space-y-4">
      <ProfilerSetupForm
        controller={controller}
        disabled={disabled}
        docker={docker}
      />
      <ProfilerWorkflow controller={controller} />
    </div>
  );
}
