import { useMemo, useState } from "react";
import {AlertTriangle, Calculator as CalculatorIcon} from "lucide-react";
import type { BenchPoint } from "../types";
import { formatSeconds } from "./charts/chartFormatters";
import {
  estimateJob,
  measuredMaxNumSeqsValues,
  sweepCompletionTokens,
  type JobInputs,
} from "../lib/jobModel";


function CalculatorField({
  id,
  label,
  value,
  onChange,
  min,
}: {
  id: string;
  label: string;
  value: number;
  onChange: (value: number) => void;
  min: number;
}) {
  return (
    <div className="inline-grid gap-1.5 align-top">
      <label className="text-xs font-medium text-muted-foreground" htmlFor={id}>
        {label}
      </label>
      <input
        className="h-9 w-32 min-w-0 rounded-md border border-input bg-background px-3 text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring"
        id={id}
        min={min}
        onChange={(event) => onChange(Math.max(min, Math.round(Number(event.target.value) || 0)))}
        step={1}
        type="number"
        value={value}
      />
    </div>
  );
}

function EstimateCard({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="rounded-md border bg-background px-3 py-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="font-mono text-lg">{value}</div>
      <div className="text-xs text-muted-foreground">{detail}</div>
    </div>
  );
}

export function BenchCalculator({ points }: { points: BenchPoint[] }) {
  // Seeded once from whatever points existed on first render; during a live sweep the user can
  // simply type the values the later servers add.
  const [maxNumSeqs, setMaxNumSeqs] = useState(() => {
    const measured = measuredMaxNumSeqsValues(points);
    return measured[measured.length - 1] ?? 4;
  });
  const [concurrentRequests, setConcurrentRequests] = useState(4);
  const [promptTokens, setPromptTokens] = useState(4096);
  const [completionTokens, setCompletionTokens] = useState(() => sweepCompletionTokens(points) ?? 128);

  const inputs = useMemo<JobInputs>(
    () => ({ maxNumSeqs, concurrentRequests, promptTokens, completionTokens }),
    [maxNumSeqs, concurrentRequests, promptTokens, completionTokens],
  );
  const estimate = useMemo(() => estimateJob(points, inputs), [points, inputs]);

  return (
    <section className="rounded-md border bg-card p-3">
      <div className="mb-3 flex items-center gap-2 text-sm font-medium">
        <CalculatorIcon className="h-4 w-4 shrink-0" />
        Job time calculator
      </div>
      <div className="flex flex-wrap gap-4">
        <CalculatorField
          id="calculator-max-num-seqs"
          label="--max-num-seqs"
          min={1}
          onChange={setMaxNumSeqs}
          value={maxNumSeqs}
        />
        <CalculatorField
          id="calculator-concurrent-requests"
          label="Concurrent requests"
          min={1}
          onChange={setConcurrentRequests}
          value={concurrentRequests}
        />
        <CalculatorField
          id="calculator-prompt-tokens"
          label="Prompt tokens"
          min={1}
          onChange={setPromptTokens}
          value={promptTokens}
        />
        <CalculatorField
          id="calculator-completion-tokens"
          label="Completion tokens"
          min={2}
          onChange={setCompletionTokens}
          value={completionTokens}
        />
      </div>
      {estimate ? (
        <>
          <div className="mt-3 grid gap-3 sm:grid-cols-3">
            <EstimateCard
              detail={`Prefill ${formatSeconds(estimate.prefillSeconds)}s + decode ${formatSeconds(estimate.decodeSeconds)}s in ${estimate.waves} wave${estimate.waves === 1 ? "" : "s"}.`}
              label="Total job duration"
              value={`${formatSeconds(estimate.durationSeconds)}s`}
            />
            <EstimateCard
              detail={`One prefill ≈ ${formatSeconds(estimate.prefillPerRequest)}s. The median request queues behind its batch-mates.`}
              label="Median TTFT (s)"
              value={`${formatSeconds(estimate.feltTtft)}s`}
            />
            <EstimateCard
              detail=""
              label="Average ITL (s)"
              value={`${formatSeconds(estimate.feltItl)}s`}
            />
          </div>
          {estimate.extrapolated ? ( <>
              <AlertTriangle className="h-4 w-4 text-amber-500" />
            <p className="mt-2 text-xs text-amber-600">
              Outside the measured range. Treat this estimate as approximate.
            </p>
          </>) : null}
        </>
      ) : (
        <p className="mt-3 text-sm text-muted-foreground">
          This profile lacks the completion timing needed for estimates. Run a new profile to enable them.
        </p>
      )}
    </section>
  );
}
