import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  Code2,
  Eraser,
  Eye,
  EyeOff,
  Loader2,
  Plus,
  RefreshCw,
  Save,
  TestTube2,
  Trash2,
  XCircle,
} from "lucide-react";
import CodeMirror from "@uiw/react-codemirror";
import { python } from "@codemirror/lang-python";
import { oneDark } from "@codemirror/theme-one-dark";
import { Button } from "./ui/button";
import { apiFetch, readJson } from "../lib/api";
import { CONFIG_QUERY_KEY, useAppConfig } from "../lib/queries";
import { SCHEMA_TYPES } from "../lib/schema";
import type {
  AppConfig,
  AsyncState,
  ConfigStatus,
  OptimizationConfig,
  ProxyConfig,
  ProxyTestResult,
  SchemaField,
} from "../types";

const PYTHON_EXTENSIONS = [python()];

const defaultConfig: AppConfig = {
  proxy: {
    base_url: "http://localhost:8000",
    api_key: "EMPTY",
    capture_bodies: true,
    max_body_bytes: 1_000_000,
    db_path: "data/request_tee.sqlite3",
  },
  validation: {
    fields: [],
  },
  deployment: {
    port: 8000,
    api_key: "EMPTY",
  },
  optimization: {
    max_mp: 0,
  },
};

type ValidatorEditorProps = {
  code: string;
  onChange: (code: string) => void;
};

function ValidatorEditor({ code, onChange }: ValidatorEditorProps) {
  return (
    <div className="grid gap-1">
      <p className="text-xs text-muted-foreground">
        Python body of a field_validator(cls, v) classmethod. The re module is available. Raise ValueError to
        flag the response. Return the validated value.
      </p>
      <CodeMirror
        basicSetup={{ lineNumbers: false, foldGutter: false }}
        className="overflow-hidden rounded-md border border-input text-xs"
        extensions={PYTHON_EXTENSIONS}
        height="160px"
        onChange={onChange}
        placeholder={"if v % 2 == 1:\n    raise ValueError(f'{v} is not even')\nreturn v"}
        theme={oneDark}
        value={code}
      />
    </div>
  );
}

export function ConfigTab() {
  const queryClient = useQueryClient();
  const configQuery = useAppConfig();

  const [config, setConfig] = useState<AppConfig>(() => configQuery.data?.app_config ?? defaultConfig);
  const [savedConfig, setSavedConfig] = useState<AppConfig>(() => configQuery.data?.app_config ?? defaultConfig);
  const [showApiKey, setShowApiKey] = useState(false);
  const [saveState, setSaveState] = useState<AsyncState>("idle");
  const [testState, setTestState] = useState<AsyncState>("idle");
  const [message, setMessage] = useState("");
  const [expandedValidators, setExpandedValidators] = useState<Set<number>>(new Set());

  const isProxyDirty = useMemo(() => JSON.stringify(config.proxy) !== JSON.stringify(savedConfig.proxy), [config.proxy, savedConfig.proxy]);
  const isValidationDirty = useMemo(
    () => JSON.stringify(config.validation) !== JSON.stringify(savedConfig.validation),
    [config.validation, savedConfig.validation],
  );
  const isOptimizationDirty = useMemo(
    () => JSON.stringify(config.optimization) !== JSON.stringify(savedConfig.optimization),
    [config.optimization, savedConfig.optimization],
  );

  useEffect(() => {
    if (configQuery.data) {
      setConfig(configQuery.data.app_config);
      setSavedConfig(configQuery.data.app_config);
    }
  }, [configQuery.data]);

  function updateProxyField<K extends keyof ProxyConfig>(field: K, value: ProxyConfig[K]) {
    setConfig((current) => ({
      ...current,
      proxy: {
        ...current.proxy,
        [field]: value,
      },
    }));
  }

  function updateOptimizationField<K extends keyof OptimizationConfig>(field: K, value: OptimizationConfig[K]) {
    setConfig((current) => ({
      ...current,
      optimization: {
        ...current.optimization,
        [field]: value,
      },
    }));
  }

  function addValidationField() {
    setConfig((current) => ({
      ...current,
      validation: {
        ...current.validation,
        fields: [...current.validation.fields, { name: "", type: "string", validator_code: "" }],
      },
    }));
  }

  function updateValidationField(index: number, patch: Partial<SchemaField>) {
    setConfig((current) => ({
      ...current,
      validation: {
        ...current.validation,
        fields: current.validation.fields.map((field, i) => (i === index ? { ...field, ...patch } : field)),
      },
    }));
  }

  function addValidatorEditor(index: number) {
    setExpandedValidators((current) => new Set(current).add(index));
  }

  function removeValidatorEditor(index: number) {
    setExpandedValidators((current) => {
      const next = new Set(current);
      next.delete(index);
      return next;
    });
    updateValidationField(index, { validator_code: "" });
  }

  function removeValidationField(index: number) {
    setConfig((current) => ({
      ...current,
      validation: {
        ...current.validation,
        fields: current.validation.fields.filter((_, i) => i !== index),
      },
    }));
    setExpandedValidators((current) => {
      const next = new Set<number>();
      for (const expandedIndex of current) {
        if (expandedIndex < index) {
          next.add(expandedIndex);
        } else if (expandedIndex > index) {
          next.add(expandedIndex - 1);
        }
      }
      return next;
    });
  }

  async function saveConfig() {
    setSaveState("loading");
    setMessage("");
    try {
      const payload = await readJson<ConfigStatus>(
        await apiFetch("/api/config", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(config),
        }),
      );
      setConfig(payload.app_config);
      setSavedConfig(payload.app_config);
      queryClient.setQueryData(CONFIG_QUERY_KEY, payload);
      void queryClient.invalidateQueries({ queryKey: ["models"] });
      setSaveState("success");
      setMessage("Settings saved.");
    } catch (error) {
      setSaveState("error");
      setMessage(error instanceof Error ? error.message : "Could not save settings");
    }
  }

  async function testConfig() {
    setTestState("loading");
    setMessage("");
    try {
      const result = await readJson<ProxyTestResult>(
        await apiFetch("/api/config/test", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ base_url: config.proxy.base_url, api_key: config.proxy.api_key }),
        }),
      );
      if (!result.ok) {
        throw new Error(result.error || "Upstream test failed");
      }
      setTestState("success");
      setMessage(result.models.length ? `Connected. Found ${result.models.length} model(s).` : "Connected. No models reported.");
    } catch (error) {
      setTestState("error");
      setMessage(error instanceof Error ? error.message : "Could not test upstream");
    }
  }

  const busy = saveState === "loading" || testState === "loading";
  const messageTone = saveState === "error" || testState === "error" ? "text-destructive" : "text-muted-foreground";

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
            {configQuery.error instanceof Error ? configQuery.error.message : "Could not load config"}
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
        <form className="space-y-5" onSubmit={(event) => event.preventDefault()}>
          <div className="space-y-1">
            <h2 className="text-base font-semibold">OpenAI endpoint</h2>
            <p className="text-sm text-muted-foreground">
              The OpenAI-compatible endpoint the proxy routes your requests to.
            </p>
          </div>

          <div className="grid gap-2">
            <label className="text-sm font-medium" htmlFor="base-url">
              Base URL
            </label>
            <input
              className="h-10 rounded-md border border-input bg-background px-3 text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring"
              id="base-url"
              inputMode="url"
              onChange={(event) => updateProxyField("base_url", event.target.value)}
              placeholder="http://localhost:8000"
              spellCheck={false}
              type="url"
              value={config.proxy.base_url}
            />
          </div>

          <div className="grid gap-2">
            <label className="text-sm font-medium" htmlFor="api-key">
              API key
            </label>
            <div className="flex gap-2">
              <input
                className="h-10 min-w-0 flex-1 rounded-md border border-input bg-background px-3 text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring"
                id="api-key"
                onChange={(event) => updateProxyField("api_key", event.target.value)}
                placeholder="EMPTY"
                spellCheck={false}
                type={showApiKey ? "text" : "password"}
                value={config.proxy.api_key}
              />
              <Button
                aria-label={showApiKey ? "Hide API key" : "Show API key"}
                onClick={() => setShowApiKey((current) => !current)}
                type="button"
                variant="outline"
                size="icon"
              >
                {showApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </Button>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2 pt-1">
            <Button disabled={busy || !config.proxy.base_url.trim()} onClick={testConfig} type="button" variant="outline">
              {testState === "loading" ? <Loader2 className="h-4 w-4 animate-spin" /> : <TestTube2 className="h-4 w-4" />}
              Test
            </Button>
            <Button disabled={busy || !isProxyDirty} onClick={saveConfig} type="button">
              {saveState === "loading" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              Save
            </Button>
            {testState === "success" ? <CheckCircle2 className="h-5 w-5 text-primary" /> : null}
            {testState === "error" || saveState === "error" ? <XCircle className="h-5 w-5 text-destructive" /> : null}
          </div>

          {message ? <p className={`text-sm ${messageTone}`}>{message}</p> : null}
        </form>

        <div className="space-y-3 border-t pt-4">
          <h2 className="text-base font-semibold">Response validation schema</h2>

          {config.validation.fields.length ? (
            <div className="space-y-3">
              {config.validation.fields.map((field, index) => {
                const hasValidator = expandedValidators.has(index) || field.validator_code.trim() !== "";
                return (
                  <div className="space-y-2 rounded-md border border-input p-3" key={index}>
                    <div className="flex items-center gap-2">
                      <input
                        className="h-10 min-w-0 flex-1 rounded-md border border-input bg-background px-3 text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring"
                        onChange={(event) => updateValidationField(index, { name: event.target.value })}
                        placeholder="field name"
                        spellCheck={false}
                        type="text"
                        value={field.name}
                      />
                      <select
                        className="h-10 w-36 rounded-md border border-input bg-background px-3 text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring"
                        onChange={(event) => updateValidationField(index, { type: event.target.value })}
                        value={field.type}
                      >
                        {SCHEMA_TYPES.map((type) => (
                          <option key={type} value={type}>
                            {type}
                          </option>
                        ))}
                      </select>
                      <Button
                        aria-label={hasValidator ? "Remove validator code" : "Add validator code"}
                        onClick={() => (hasValidator ? removeValidatorEditor(index) : addValidatorEditor(index))}
                        type="button"
                        variant="outline"
                        size="icon"
                      >
                        {hasValidator ? <Eraser className="h-4 w-4" /> : <Code2 className="h-4 w-4" />}
                      </Button>
                      <Button
                        aria-label="Remove field"
                        onClick={() => removeValidationField(index)}
                        type="button"
                        variant="outline"
                        size="icon"
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                    {hasValidator ? (
                      <ValidatorEditor
                        code={field.validator_code}
                        onChange={(code) => updateValidationField(index, { validator_code: code })}
                      />
                    ) : null}
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">No fields defined. Responses will not be validated.</p>
          )}

          <div className="flex flex-wrap items-center gap-2 pt-1">
            <Button onClick={addValidationField} type="button" variant="outline" size="sm">
              <Plus className="h-4 w-4" />
              Add field
            </Button>
            <Button disabled={busy || !isValidationDirty} onClick={saveConfig} type="button">
              {saveState === "loading" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              Save
            </Button>
          </div>
        </div>

        <div className="space-y-3 border-t pt-4">
          <h2 className="text-base font-semibold">Optimization</h2>

          <div className="grid gap-2">
            <label className="text-sm font-medium" htmlFor="optimization-max-mp">
              Max megapixels per image
            </label>
            <p className="text-xs text-muted-foreground">
              Images in requests larger than this many total megapixels are downscaled (aspect ratio
              preserved) before being forwarded to the OpenAI endpoint. Set to 0 to disable.
            </p>
            <input
              className="h-10 w-48 rounded-md border border-input bg-background px-3 text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring"
              id="optimization-max-mp"
              min={0}
              onChange={(event) => updateOptimizationField("max_mp", Math.max(0, Number(event.target.value) || 0))}
              step={0.1}
              type="number"
              value={config.optimization.max_mp}
            />
          </div>

          <div className="flex flex-wrap items-center gap-2 pt-1">
            <Button disabled={busy || !isOptimizationDirty} onClick={saveConfig} type="button">
              {saveState === "loading" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              Save
            </Button>
          </div>
        </div>
      </div>
    </section>
  );
}
