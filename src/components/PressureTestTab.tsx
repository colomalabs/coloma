import {useState} from "react";
import {useMutation} from "@tanstack/react-query";
import {AlertTriangle, Loader2, Play} from "lucide-react";
import {Button} from "./ui/button";
import {apiFetch, readJson} from "../lib/api";
import { formatSeconds, formatTokens } from "./charts/chartFormatters";
import {isJobActive, useActiveProfilerJob} from "../lib/queries";
import type {PressureTestRequest, PressureTestResult} from "../types";

const DEFAULT_FORM: PressureTestRequest = {
    prompt_tokens: 1024,
    num_seqs: 8,
    completion_tokens: 64,
    ttft_timeout: 30,
};

const FIELDS: Array<{ key: keyof PressureTestRequest; label: string; min: number; max: number }> = [
    {key: "prompt_tokens", label: "Prompt length", min: 1, max: 1_000_000},
    {key: "num_seqs", label: "Concurrency", min: 1, max: 1024},
    {key: "completion_tokens", label: "Completion length", min: 1, max: 4096},
    {key: "ttft_timeout", label: "TTFT timeout", min: 1, max: 3600},
];

function Stat({label, value, unit, alert = false}: { label: string; value: string; unit: string; alert?: boolean }) {
    return (
        <div className="rounded-md border bg-card px-4 py-3">
            <p className="text-xs text-muted-foreground">{label}</p>
            <p className={`mt-1 font-mono text-lg font-medium ${alert ? "text-destructive" : ""}`}>
                {value}
                <span className="ml-1 text-xs font-normal text-muted-foreground">{unit}</span>
            </p>
        </div>
    );
}

function Results({result}: { result: PressureTestResult }) {
    const failed = result.failures > 0;
    return (
        <div className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-3">
                <Stat label="Average ITL" unit="s" value={formatSeconds(result.average_itl)}/>
                <Stat label="System throughput" unit="tok/s" value={formatTokens(result.system_throughput)}/>
                <Stat label="Median prompt" unit="tok" value={result.median_prompt_tokens.toLocaleString()}/>
                <Stat label="Median TTFT" unit="s" value={formatSeconds(result.median_ttft)}/>
                <Stat label="p95 TTFT" unit="s" value={formatSeconds(result.p95_ttft)}/>
                <Stat label="Highest TTFT" unit="s" value={formatSeconds(result.max_ttft)}/>
                <Stat label="System decoding throughput" unit="tok/s" value={formatTokens(result.system_decoding_throughput)}/>
                <Stat label="Batch duration" unit="s" value={formatSeconds(result.duration)}/>
                <Stat
                    alert={failed}
                    label="Failed requests"
                    unit={`of ${result.num_seqs}`}
                    value={result.failures.toString()}
                />
            </div>
            {failed ? (
                <div className="flex items-start gap-2 rounded-md border bg-card px-4 py-3 text-sm text-destructive">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0"/>
                    <span>
            The stats cover the {result.samples.length} requests that completed. {result.error}
          </span>
                </div>
            ) : null}
        </div>
    );
}

export function PressureTestTab() {
    const [form, setForm] = useState<PressureTestRequest>(DEFAULT_FORM);

    const {data: activeProfilerJob} = useActiveProfilerJob();

    const runMutation = useMutation({
        mutationFn: async (payload: PressureTestRequest) =>
            readJson<PressureTestResult>(
                await apiFetch("/api/pressure/run", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify(payload),
                }),
            ),
    });

    const profilerRunning = isJobActive(activeProfilerJob);
    const result = runMutation.data;
    const errorMessage = runMutation.error
        ? runMutation.error instanceof Error
            ? runMutation.error.message
            : "Pressure test failed"
        : "";

    return (
        <section className="mx-auto w-full max-w-[1600px] px-5 py-5">
            <div className="max-w-3xl space-y-5">
                <h2 className="text-base font-semibold">Pressure test</h2>
                <form
                    className="space-y-4"
                    onSubmit={(event) => {
                        event.preventDefault();
                        if (profilerRunning || runMutation.isPending) return;
                        runMutation.mutate(form);
                    }}
                >
                    {FIELDS.map((field) => (
                        <div className="space-y-1" key={field.key}>
                            <label className="text-sm font-medium" htmlFor={`pressure-${field.key}`}>
                                {field.label}
                            </label>
                            <input
                                className="h-10 w-full min-w-0 rounded-md border border-input bg-background px-3 text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
                                disabled={runMutation.isPending}
                                id={`pressure-${field.key}`}
                                max={field.max}
                                min={field.min}
                                onChange={(event) => setForm((current) => ({
                                    ...current,
                                    [field.key]: Number(event.target.value)
                                }))}
                                type="number"
                                value={form[field.key]}
                            />
                        </div>
                    ))}

                    <Button disabled={runMutation.isPending || profilerRunning} type="submit">
                        {runMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin"/> :
                            <Play className="h-4 w-4"/>}
                        Run
                    </Button>
                    {errorMessage ? <p className="text-xs text-destructive">{errorMessage}</p> : null}
                </form>

                {result && (<Results result={result}/>)}

            </div>
        </section>
    );
}
