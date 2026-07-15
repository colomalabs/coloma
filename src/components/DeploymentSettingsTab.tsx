import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Eye, EyeOff, Loader2, RefreshCw, Save } from "lucide-react";
import { apiFetch, readJson } from "../lib/api";
import { CONFIG_QUERY_KEY, useAppConfig } from "../lib/queries";
import type { AsyncState, ConfigStatus, DeploymentConfig } from "../types";
import { Button } from "./ui/button";

const DEFAULT_DEPLOYMENT_CONFIG: DeploymentConfig = { port: 8000, api_key: "EMPTY" };

function proxyTarget(baseUrl: string): { host: string; port: number | null } {
  try {
    const url = new URL(baseUrl);
    const defaultPort = url.protocol === "https:" ? 443 : url.protocol === "http:" ? 80 : null;
    return { host: url.hostname, port: url.port ? Number(url.port) : defaultPort };
  } catch {
    return { host: "", port: null };
  }
}

export function DeploymentSettingsTab() {
  const queryClient = useQueryClient();
  const configQuery = useAppConfig();
  const [settings, setSettings] = useState<DeploymentConfig>(
    () => configQuery.data?.app_config.deployment ?? DEFAULT_DEPLOYMENT_CONFIG,
  );
  const [showApiKey, setShowApiKey] = useState(false);
  const [saveState, setSaveState] = useState<AsyncState>("idle");
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (configQuery.data) setSettings(configQuery.data.app_config.deployment);
  }, [configQuery.data]);

  const savedSettings = configQuery.data?.app_config.deployment;
  const dirty = savedSettings ? JSON.stringify(settings) !== JSON.stringify(savedSettings) : false;
  const warnings = useMemo(() => {
    const proxy = configQuery.data?.app_config.proxy;
    if (!proxy) return [];

    const target = proxyTarget(proxy.base_url);
    const next: string[] = [];
    if (target.host && !["localhost", "127.0.0.1", "::1"].includes(target.host)) {
      next.push(`The proxy Base URL points to ${target.host}, not the locally deployed vLLM server.`);
    }
    if (target.port !== null && target.port !== settings.port) {
      next.push(`The proxy Base URL uses port ${target.port}, but deployment uses port ${settings.port}.`);
    }
    if (proxy.api_key !== settings.api_key) {
      next.push("The deployment API key does not match the proxy API key.");
    }
    return next;
  }, [configQuery.data?.app_config.proxy, settings]);

  async function save() {
    const current = configQuery.data?.app_config;
    if (!current) return;
    setSaveState("loading");
    setMessage("");
    try {
      const payload = await readJson<ConfigStatus>(
        await apiFetch("/api/config", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...current, deployment: settings }),
        }),
      );
      queryClient.setQueryData(CONFIG_QUERY_KEY, payload);
      setSettings(payload.app_config.deployment);
      setSaveState("success");
      setMessage("Deployment settings saved.");
    } catch (error) {
      setSaveState("error");
      setMessage(error instanceof Error ? error.message : "Could not save deployment settings");
    }
  }

  const saving = saveState === "loading";
  const messageTone = saveState === "error" ? "text-destructive" : "text-muted-foreground";

  if (configQuery.isPending) {
    return (
      <section className="mx-auto w-full max-w-[1600px] px-5 py-5">
        <div className="max-w-3xl space-y-5">
          <div className="grid gap-2">
            <div className="h-4 w-16 animate-pulse rounded bg-muted" />
            <div className="h-10 w-full animate-pulse rounded-md bg-muted" />
          </div>
          <div className="grid gap-2">
            <div className="h-4 w-16 animate-pulse rounded bg-muted" />
            <div className="h-10 w-full animate-pulse rounded-md bg-muted" />
          </div>
          <div className="flex gap-2 pt-1">
            <div className="h-10 w-24 animate-pulse rounded-md bg-muted" />
          </div>
        </div>
      </section>
    );
  }

  if (configQuery.isError) {
    return (
      <section className="mx-auto w-full max-w-[1600px] px-5 py-5">
        <div className="max-w-3xl space-y-5">
          <p className="text-sm text-destructive">
            {configQuery.error instanceof Error ? configQuery.error.message : "Could not load deployment settings"}
          </p>
          <Button onClick={() => void configQuery.refetch()} type="button" variant="outline">
            <RefreshCw className="h-4 w-4" />
            Retry
          </Button>
        </div>
      </section>
    );
  }

  return (
    <section className="mx-auto w-full max-w-[1600px] px-5 py-5">
      <div className="max-w-3xl space-y-5">
        <div className="space-y-1">
          <h2 className="text-base font-semibold">Deployment settings</h2>
          <p className="text-sm text-muted-foreground">
            Used for profiling and pressure testing vLLM servers.
          </p>
        </div>

        <div className="grid gap-2">
          <label className="text-sm font-medium" htmlFor="deployment-port">
            Port
          </label>
          <input
            className="h-10 w-48 rounded-md border border-input bg-background px-3 text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring"
            id="deployment-port"
            max={65535}
            min={1}
            onChange={(event) => setSettings((current) => ({
              ...current,
              port: Math.max(1, Math.min(65535, Math.round(Number(event.target.value) || 0))),
            }))}
            type="number"
            value={settings.port}
          />
        </div>

        <div className="grid gap-2">
          <label className="text-sm font-medium" htmlFor="deployment-api-key">API key</label>
          <div className="flex gap-2">
            <input
              className="h-10 min-w-0 flex-1 rounded-md border border-input bg-background px-3 text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring"
              id="deployment-api-key"
              onChange={(event) => setSettings((current) => ({ ...current, api_key: event.target.value }))}
              placeholder="EMPTY"
              spellCheck={false}
              type={showApiKey ? "text" : "password"}
              value={settings.api_key}
            />
            <Button
              aria-label={showApiKey ? "Hide API key" : "Show API key"}
              onClick={() => setShowApiKey((current) => !current)}
              size="icon"
              type="button"
              variant="outline"
            >
              {showApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </Button>
          </div>
        </div>

        {warnings.length ? (
          <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/5 px-4 py-3 text-sm">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
            <div className="space-y-1">
              <p className="font-medium">Deployment and proxy settings do not match</p>
              {warnings.map((warning) => <p className="text-muted-foreground" key={warning}>{warning}</p>)}
            </div>
          </div>
        ) : null}

        <div className="flex flex-wrap items-center gap-2 pt-1">
          <Button disabled={!dirty || saving} onClick={() => void save()} type="button">
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            Save
          </Button>
        </div>

        {message ? <p className={`text-sm ${messageTone}`}>{message}</p> : null}
      </div>
    </section>
  );
}
