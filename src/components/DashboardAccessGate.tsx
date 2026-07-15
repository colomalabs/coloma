import { useState } from "react";
import { KeyRound } from "lucide-react";
import { getStoredApiKey } from "../lib/api";
import { MetricInfo } from "./charts/SeriesChart";
import { Button } from "./ui/button";

type DashboardAccessGateProps = {
  invalidKey: boolean;
  onSubmit: (apiKey: string) => void;
};

export function DashboardAccessGate({ invalidKey, onSubmit }: DashboardAccessGateProps) {
  const [apiKey, setApiKey] = useState(getStoredApiKey);

  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-5 text-foreground">
      <form
        className="w-full max-w-sm space-y-4 rounded-md border bg-card p-5"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit(apiKey.trim());
        }}
      >
        <div className="flex items-center gap-2">
          <KeyRound className="h-5 w-5 text-muted-foreground" />
          <h1 className="text-lg font-semibold">Coloma</h1>
        </div>
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <label className="text-sm font-medium" htmlFor="dashboard-api-key">
              Dashboard API key
            </label>
            <MetricInfo description="The COLOMA_API_KEY from backend/.env, also used by clients to authenticate to the proxy." />
          </div>
          <input
            autoFocus
            className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring"
            id="dashboard-api-key"
            onChange={(event) => setApiKey(event.target.value)}
            placeholder="COLOMA_API_KEY"
            spellCheck={false}
            type="password"
            value={apiKey}
          />
        </div>
        {invalidKey ? <p className="text-xs text-destructive">That API key was not accepted.</p> : null}
        <Button className="w-full" disabled={!apiKey.trim()} type="submit">
          Continue
        </Button>
      </form>
    </main>
  );
}
