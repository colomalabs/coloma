import { useEffect, useState } from "react";
import { Loader2, RotateCcw } from "lucide-react";
import type { ProfilerJobSnapshot } from "../../types";
import { Button } from "../ui/button";

function DeployField({
  id,
  label,
  value,
  min,
  max,
  disabled,
  onChange,
}: {
  id: string;
  label: string;
  value: number;
  min?: number;
  max?: number;
  disabled: boolean;
  onChange: (value: number) => void;
}) {
  return (
    <div className="grid gap-1.5">
      <label className="text-sm font-medium" htmlFor={id}>
        <code className="font-mono">{label}</code>
      </label>
      <input
        className="h-10 w-40 min-w-0 rounded-md border border-input bg-background px-3 text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
        disabled={disabled}
        id={id}
        max={max}
        min={min}
        onChange={(event) => onChange(Math.round(Number(event.target.value) || 0))}
        type="number"
        value={value}
      />
    </div>
  );
}

export function DeployConfigForm({
  serverMaxModelLen,
  defaultMaxNumSeqs,
  disabled,
  onChoose,
  error,
}: {
  serverMaxModelLen: number;
  defaultMaxNumSeqs: number;
  disabled: boolean;
  onChoose: (maxNumSeqs: number, maxModelLen: number) => void;
  error: string;
}) {
  const [maxNumSeqs, setMaxNumSeqs] = useState(defaultMaxNumSeqs);
  const [maxModelLen, setMaxModelLen] = useState(serverMaxModelLen);

  return (
    <div className="space-y-4">
      <DeployField
        disabled={disabled}
        id="deploy-max-num-seqs"
        label="--max-num-seqs"
        onChange={setMaxNumSeqs}
        value={maxNumSeqs}
      />
      <DeployField
        disabled={disabled}
        id="deploy-max-model-len"
        label="--max-model-len"
        onChange={setMaxModelLen}
        value={maxModelLen}
      />
      <Button
        disabled={disabled}
        onClick={() => onChoose(maxNumSeqs, maxModelLen)}
        size="sm"
        type="button"
      >
        Submit
      </Button>
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
    </div>
  );
}

export function OomRecoveryForm({
  options,
  disabled,
  pendingAction,
  error,
  onDeploy,
  onRetry,
  onSaveAndRetry,
}: {
  options: NonNullable<ProfilerJobSnapshot["oom_recovery"]>;
  disabled: boolean;
  pendingAction: "deploy" | "retry" | "save_retry" | null;
  error: string;
  onDeploy: (maxNumSeqs: number, maxModelLen: number) => void;
  onRetry: () => void;
  onSaveAndRetry: (maxNumSeqs: number, maxModelLen: number) => void;
}) {
  const [maxNumSeqs, setMaxNumSeqs] = useState(options.max_num_seqs);
  const [maxModelLen, setMaxModelLen] = useState(options.max_model_len);

  useEffect(() => {
    setMaxNumSeqs(options.max_num_seqs);
    setMaxModelLen(options.max_model_len);
  }, [options.max_num_seqs, options.max_model_len]);

  const retryLabel = `at --max-model-len = ${options.retry_max_model_len.toLocaleString()}`;

  return (
    <div className="space-y-4">
      <DeployField
        disabled={disabled}
        id="oom-recovery-max-num-seqs"
        label="--max-num-seqs"
        onChange={setMaxNumSeqs}
        value={maxNumSeqs}
      />
      <DeployField
        disabled={disabled}
        id="oom-recovery-max-model-len"
        label="--max-model-len"
        onChange={setMaxModelLen}
        value={maxModelLen}
      />
      <div className="flex flex-wrap items-center gap-2">
        <Button disabled={disabled} onClick={() => onDeploy(maxNumSeqs, maxModelLen)} size="sm" type="button">
          {pendingAction === "deploy" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
          Save
        </Button>
        {options.savable ? (
          // The completed --max-num-seqs is worth keeping, so retrying smaller saves it as a profile first.
          <Button
            disabled={disabled}
            onClick={() => onSaveAndRetry(maxNumSeqs, maxModelLen)}
            size="sm"
            type="button"
            variant="outline"
          >
            {pendingAction === "save_retry" ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RotateCcw className="h-3.5 w-3.5" />
            )}
            Save and retry {retryLabel}
          </Button>
        ) : (
          <Button disabled={disabled} onClick={onRetry} size="sm" type="button" variant="outline">
            {pendingAction === "retry" ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RotateCcw className="h-3.5 w-3.5" />
            )}
            Retry {retryLabel}
          </Button>
        )}
      </div>
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
    </div>
  );
}
