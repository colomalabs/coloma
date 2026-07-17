import { useState } from "react";
import { AlertTriangle, Gauge, Loader2, Plus, RotateCcw, X, XCircle } from "lucide-react";
import type { DockerPullStatus, ProfilerDefaults } from "../../types";
import { Button } from "../ui/button";
import { InfoNotice } from "../ui/info-notice";
import { DockerImageSelector } from "./DockerImageSelector";
import type { ProfilerController } from "./useProfilerController";

const DEFAULT_COMPLETION_TOKENS = 64;

export type ProfilerDockerState = {
  error: string;
  images: string[];
  isPending: boolean;
  pullStatus: DockerPullStatus;
  targetImage: string;
};

type ProfilerSetupFormProps = {
  controller: ProfilerController;
  defaults: ProfilerDefaults;
  disabled: boolean;
  disabledReason?: string;
  docker: ProfilerDockerState;
};

export function ProfilerSetupForm({
  controller,
  defaults,
  disabled,
  disabledReason,
  docker,
}: ProfilerSetupFormProps) {
  const [selectedImage, setSelectedImage] = useState("");
  const [model, setModel] = useState("");
  const [fp8, setFp8] = useState(true);
  const [autoMaxModelLen, setAutoMaxModelLen] = useState(true);
  const [maxModelLen, setMaxModelLen] = useState(16384);
  const [extraVllmArgs, setExtraVllmArgs] = useState("");
  const [ttftTimeout, setTtftTimeout] = useState(30);
  const [stressTestTimeout, setStressTestTimeout] = useState(180);
  const [completionTokens, setCompletionTokens] = useState(DEFAULT_COMPLETION_TOKENS);
  const [maxNumSeqsValues, setMaxNumSeqsValues] = useState(() => [...defaults.max_num_seqs_values]);
  const [concurrentRequestValues, setConcurrentRequestValues] = useState(() => [
    ...defaults.concurrent_request_values,
  ]);
  const inputsDisabled = controller.running || disabled;

  const start = () => {
    const modelName = model.trim();
    if (!modelName || !selectedImage || controller.running) return;
    controller.start({
      modelName,
      imageTag: selectedImage,
      fp8,
      maxModelLen: autoMaxModelLen ? null : maxModelLen,
      extraVllmArgs,
      ttftTimeout,
      stressTestTimeout,
      completionTokens,
      maxNumSeqsValues,
      concurrentRequestValues,
    });
  };

  return (
    <div className="space-y-4">
      {disabled && disabledReason ? <InfoNotice>{disabledReason}</InfoNotice> : null}
      {docker.isPending ? (
        <div className="h-10 animate-pulse rounded-md border bg-muted" />
      ) : docker.error ? (
        <div className="flex items-center gap-2 rounded-md border bg-card px-4 py-3 text-sm text-muted-foreground">
          <AlertTriangle className="h-4 w-4" />
          {docker.error}
        </div>
      ) : (
        <DockerImageSelector
          disabled={inputsDisabled}
          images={docker.images}
          pullStatus={docker.pullStatus}
          selectedImage={selectedImage}
          setSelectedImage={setSelectedImage}
          targetImage={docker.targetImage}
        />
      )}

      <div className="grid gap-2">
        <label className="text-sm font-medium" htmlFor="profile-model">
          Hugging Face model
        </label>
        <input
          className="h-10 min-w-0 rounded-md border border-input bg-background px-3 text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring"
          disabled={inputsDisabled}
          id="profile-model"
          onChange={(event) => setModel(event.target.value)}
          placeholder="e.g. Qwen/Qwen3.5-35B-A3B-FP8"
          spellCheck={false}
          type="text"
          value={model}
        />

        <div className="grid gap-2">
          <div className="text-sm font-medium">Flags</div>
          <label className="flex w-fit items-center gap-2 text-sm text-foreground" htmlFor="profile-fp8">
            <input
              checked={fp8}
              className="h-4 w-4 rounded border-input"
              disabled={inputsDisabled}
              id="profile-fp8"
              onChange={(event) => setFp8(event.target.checked)}
              type="checkbox"
            />
            FP8 kv cache and quantization
          </label>

          <label className="flex w-fit items-center gap-2 text-sm text-foreground" htmlFor="profile-auto-max-model-len">
            <input
              checked={autoMaxModelLen}
              className="h-4 w-4 rounded border-input"
              disabled={inputsDisabled}
              id="profile-auto-max-model-len"
              onChange={(event) => setAutoMaxModelLen(event.target.checked)}
              type="checkbox"
            />
            Auto --max-model-len (the model&apos;s own maximum)
          </label>
          {!autoMaxModelLen ? (
            <div className="grid w-fit gap-1">
              <NumberField
                disabled={inputsDisabled}
                id="profile-max-model-len"
                label="--max-model-len"
                onChange={setMaxModelLen}
                value={maxModelLen}
              />
              {maxModelLen < 2048 ? (
                <p className="text-xs text-amber-600">vLLM may not start below 2,048 tokens.</p>
              ) : null}
            </div>
          ) : null}

          <div className="grid gap-1.5">
            <label className="text-sm font-medium" htmlFor="profile-extra-vllm-args">
              Extra vLLM args
            </label>
            <input
              className="h-10 min-w-0 rounded-md border border-input bg-background px-3 text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring"
              disabled={inputsDisabled}
              id="profile-extra-vllm-args"
              onChange={(event) => setExtraVllmArgs(event.target.value)}
              placeholder="e.g. --tokenizer-mode mistral"
              spellCheck={false}
              type="text"
              value={extraVllmArgs}
            />
          </div>
        </div>

        <details className="mt-2 rounded-md border bg-card">
          <summary className="cursor-pointer px-4 py-3 text-sm font-medium">
            Advanced settings
          </summary>
          <div className="grid gap-5 border-t px-4 py-4">
            <div className="flex flex-wrap gap-4">
              <NumberField
                disabled={inputsDisabled}
                id="profile-ttft-timeout"
                label="TTFT timeout (s)"
                onChange={setTtftTimeout}
                value={ttftTimeout}
              />
              <NumberField
                disabled={inputsDisabled}
                id="profile-stress-test-timeout"
                label="Stress test timeout (s)"
                onChange={setStressTestTimeout}
                value={stressTestTimeout}
              />
              <NumberField
                disabled={inputsDisabled}
                id="profile-completion-tokens"
                label="Completion tokens"
                min={2}
                onChange={setCompletionTokens}
                value={completionTokens}
              />
            </div>
            <BenchmarkValueEditor
              description=""
              disabled={inputsDisabled}
              id="profile-max-num-seqs-values"
              label="Server batch sizes (--max-num-seqs)"
              onChange={setMaxNumSeqsValues}
              values={maxNumSeqsValues}
            />
            <BenchmarkValueEditor
              description=""
              disabled={inputsDisabled}
              id="profile-concurrent-request-values"
              label="Concurrent requests"
              onChange={setConcurrentRequestValues}
              values={concurrentRequestValues}
            />
            <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-4">
              <p className="text-xs text-muted-foreground">
                {maxNumSeqsValues.length} server configurations × {concurrentRequestValues.length} request levels
              </p>
              <Button
                disabled={inputsDisabled}
                onClick={() => {
                  setMaxNumSeqsValues([...defaults.max_num_seqs_values]);
                  setConcurrentRequestValues([...defaults.concurrent_request_values]);
                  setCompletionTokens(DEFAULT_COMPLETION_TOKENS);
                }}
                size="sm"
                type="button"
                variant="ghost"
              >
                <RotateCcw className="h-3.5 w-3.5" />
                Restore defaults
              </Button>
            </div>
          </div>
        </details>

        <div className="mt-2">
          {controller.running ? (
            <Button
              disabled={controller.cancelPending}
              onClick={controller.cancel}
              type="button"
              variant="destructive"
            >
              {controller.cancelPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <XCircle className="h-4 w-4" />
              )}
              Cancel
            </Button>
          ) : (
            <Button
              disabled={disabled || !model.trim() || !selectedImage || controller.startPending}
              onClick={start}
              type="button"
            >
              {controller.startPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Gauge className="h-4 w-4" />
              )}
              Profile
            </Button>
          )}
        </div>
        {!selectedImage ? (
          <p className="text-xs text-muted-foreground">Select a vLLM Docker image above to enable profiling.</p>
        ) : null}
        {controller.startError ? <p className="text-xs text-destructive">{controller.startError}</p> : null}
      </div>
    </div>
  );
}

function BenchmarkValueEditor({
  id,
  label,
  description,
  values,
  disabled,
  onChange,
}: {
  id: string;
  label: string;
  description: string;
  values: number[];
  disabled: boolean;
  onChange: (values: number[]) => void;
}) {
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");

  const addValue = () => {
    const value = Number(draft);
    if (!Number.isInteger(value) || value < 1) {
      setError("Enter a positive whole number.");
      return;
    }
    if (values.includes(value)) {
      setError(`${value} is already included.`);
      return;
    }
    onChange([...values, value].sort((left, right) => left - right));
    setDraft("");
    setError("");
  };

  return (
    <div className="grid gap-2">
      <div>
        <label className="text-sm font-medium" htmlFor={id}>{label}</label>
        <p className="text-xs text-muted-foreground">{description}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        {values.map((value) => (
          <span
            className="inline-flex h-8 items-center gap-1 rounded-full border bg-background pl-3 pr-1 text-sm"
            key={value}
          >
            {value}
            <button
              aria-label={`Remove ${value} from ${label}`}
              className="rounded-full p-1 text-muted-foreground hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
              disabled={disabled || values.length === 1}
              onClick={() => onChange(values.filter((candidate) => candidate !== value))}
              type="button"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </span>
        ))}
      </div>
      <div className="flex items-center gap-2">
        <input
          className="h-9 w-28 min-w-0 rounded-md border border-input bg-background px-3 text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring"
          disabled={disabled}
          id={id}
          min={1}
          onChange={(event) => {
            setDraft(event.target.value);
            setError("");
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              addValue();
            }
          }}
          placeholder="Add value"
          step={1}
          type="number"
          value={draft}
        />
        <Button disabled={disabled || !draft} onClick={addValue} size="sm" type="button" variant="outline">
          <Plus className="h-3.5 w-3.5" />
          Add
        </Button>
      </div>
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
      {values.length === 1 ? <p className="text-xs text-muted-foreground">At least one value is required.</p> : null}
    </div>
  );
}

function NumberField({
  id,
  label,
  value,
  disabled,
  onChange,
  min = 1,
}: {
  id: string;
  label: string;
  value: number;
  disabled: boolean;
  onChange: (value: number) => void;
  min?: number;
}) {
  return (
    <div className="inline-grid gap-1.5 align-top">
      <label className="text-sm font-medium" htmlFor={id}>
        {label}
      </label>
      <input
        className="h-10 w-32 min-w-0 rounded-md border border-input bg-background px-3 text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring"
        disabled={disabled}
        id={id}
        min={min}
        onChange={(event) => onChange(Math.max(min, Math.round(Number(event.target.value) || 0)))}
        type="number"
        value={value}
      />
    </div>
  );
}
