import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { Dashboard } from "./components/Dashboard";
import { DashboardAccessGate } from "./components/DashboardAccessGate";
import { apiFetch, getStoredApiKey, readJson, setStoredApiKey, UnauthorizedError, UNAUTHORIZED_EVENT } from "./lib/api";

export function App() {
  const [authAttempt, setAuthAttempt] = useState(0);
  const [reauthRequired, setReauthRequired] = useState(false);
  const [keyRejected, setKeyRejected] = useState(false);

  const authQuery = useQuery({
    queryKey: ["auth", authAttempt],
    queryFn: async () => readJson<{ ok: boolean }>(await apiFetch("/api/auth/check")),
    retry: false,
    staleTime: Infinity,
  });

  useEffect(() => {
    if (authQuery.error instanceof UnauthorizedError) {
      setReauthRequired(true);
      // A rejected check that carried a key means that key is wrong. One that carried
      // none has nothing to reject: the gate is only asking for a key, not refusing it.
      setKeyRejected(getStoredApiKey() !== "");
    }
  }, [authQuery.error]);

  useEffect(() => {
    const requireAuthentication = () => setReauthRequired(true);
    window.addEventListener(UNAUTHORIZED_EVENT, requireAuthentication);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, requireAuthentication);
  }, []);

  const requiresAuthentication = reauthRequired || authQuery.error instanceof UnauthorizedError;

  if (authQuery.isPending) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-background text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" aria-label="Checking dashboard access" />
      </main>
    );
  }

  if (requiresAuthentication) {
    return (
      <DashboardAccessGate
        invalidKey={keyRejected}
        onSubmit={(apiKey) => {
          setStoredApiKey(apiKey);
          setReauthRequired(false);
          setKeyRejected(false);
          setAuthAttempt((current) => current + 1);
        }}
      />
    );
  }

  return <Dashboard />;
}
