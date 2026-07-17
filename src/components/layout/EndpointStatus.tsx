import { useUpstreamStatus } from "../../lib/queries";

// "waiting" = the endpoint is reachable but reports no models yet (e.g. a deployment whose HTTP
// server is up before the model is registered). It is deliberately kept distinct from "connected"
// so the light only turns green once the Chat tab can actually find a model to talk to.
type ConnectionState = "checking" | "connected" | "waiting" | "disconnected";

export function EndpointStatus() {
  const { data, error } = useUpstreamStatus();

  let state: ConnectionState = "checking";
  let message = "Checking connection to the OpenAI endpoint…";
  if (data) {
    if (!data.connected) {
      state = "disconnected";
      message = data.error || "Could not reach the OpenAI endpoint";
    } else if (data.model_count > 0) {
      state = "connected";
      message = data.detail || "Connected to the OpenAI endpoint";
    } else {
      state = "waiting";
      message = "Endpoint reachable, waiting for a model to come online…";
    }
  } else if (error) {
    state = "disconnected";
    message = error instanceof Error ? error.message : "Could not reach the OpenAI endpoint";
  }

  const dotClass =
    state === "connected"
      ? "bg-primary"
      : state === "disconnected"
        ? "bg-destructive"
        : "bg-muted-foreground";
  const label =
    state === "connected"
      ? "OpenAI endpoint connected"
      : state === "disconnected"
        ? "OpenAI endpoint disconnected"
        : state === "waiting"
          ? "Waiting for a model…"
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
