import { useUpstreamStatus } from "../../lib/queries";

type ConnectionState = "checking" | "connected" | "disconnected";

export function EndpointStatus() {
  const { data, error } = useUpstreamStatus();

  let state: ConnectionState = "checking";
  let message = "Checking connection to the OpenAI endpoint…";
  if (data) {
    state = data.connected ? "connected" : "disconnected";
    message = data.connected
      ? data.detail || "Connected to the OpenAI endpoint"
      : data.error || "Could not reach the OpenAI endpoint";
  } else if (error) {
    state = "disconnected";
    message = error instanceof Error ? error.message : "Could not reach the OpenAI endpoint";
  }

  const dotClass =
    state === "connected" ? "bg-primary" : state === "disconnected" ? "bg-destructive" : "bg-muted-foreground";
  const label =
    state === "connected"
      ? "OpenAI endpoint connected"
      : state === "disconnected"
        ? "OpenAI endpoint disconnected"
        : "Checking OpenAI endpoint…";

  return (
    <div className="flex items-center gap-2 text-xs text-muted-foreground p-1.5" title={message}>
      <span
        aria-hidden="true"
        className={`h-2.5 w-2.5 shrink-0 rounded-full ${dotClass} ${state !== "disconnected" ? "animate-pulse" : ""}`}
      />
      <span>{label}</span>
    </div>
  );
}
