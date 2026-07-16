import type { ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Loader2, Pencil, SkipForward, XCircle } from "lucide-react";
import type { ProfilerStep } from "../../types";
import { BenchCalculator } from "../BenchCalculator";
import { BenchCharts } from "../BenchCharts";
import { Button } from "../ui/button";
import { ContextLengthWarnings } from "./ContextLengthWarnings";
import { DeployConfigForm, OomRecoveryForm } from "./DeployConfigurationForms";
import { WarningNotice } from "./WarningNotice";
import type { ProfilerController } from "./useProfilerController";

// "warning" is a display-only state (a finished-but-imperfect sweep); it is not a real ProfilerStepStatus.
// "editing" is a display-only state for a step that's idle, waiting on user input rather than working.
type IconStatus = ProfilerStep["status"] | "warning" | "editing";

function StepIcon({ status }: { status: IconStatus }) {
  if (status === "running") return <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />;
  if (status === "editing") return <Pencil className="h-4 w-4 text-muted-foreground" />;
  if (status === "done") return <CheckCircle2 className="h-4 w-4 text-emerald-600" />;
  if (status === "warning") return <AlertTriangle className="h-4 w-4 text-amber-500" />;
  if (status === "error") return <XCircle className="h-4 w-4 text-destructive" />;
  return <div className="h-4 w-4 rounded-full border border-muted-foreground/40" />;
}

function StepCard({
  title,
  step,
  icon,
  showDetail = true,
  action,
  children,
}: {
  title: ReactNode;
  step: ProfilerStep;
  // Overrides the icon derived from step.status, e.g. a finished-but-imperfect benchmark.
  icon?: IconStatus;
  showDetail?: boolean;
  action?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="space-y-3 rounded-md border bg-card px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-medium">
          <StepIcon status={icon ?? step.status} />
          {title}
        </div>
        {action}
      </div>
      {showDetail && step.detail ? <p className="text-sm text-muted-foreground">{step.detail}</p> : null}
      {step.status === "error" && step.error ? (
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-md border border-destructive/40 bg-background p-3 text-xs text-destructive">
          {step.error}
        </pre>
      ) : null}
      {children}
    </div>
  );
}

function BenchmarkProgress({ done, total }: { done: number; total: number }) {
  if (total <= 0) return null;
  const percent = Math.min(100, Math.round((done / total) * 100));

  return (
    <div className="flex items-center gap-3">
      <div
        aria-valuemax={total}
        aria-valuemin={0}
        aria-valuenow={done}
        className="h-2 min-w-20 flex-1 overflow-hidden rounded-full bg-muted"
        role="progressbar"
      >
        <div
          className="h-full rounded-full bg-emerald-500 transition-[width] duration-500 ease-out"
          style={{ width: `${percent}%` }}
        />
      </div>
      <p className="shrink-0 whitespace-nowrap text-xs text-muted-foreground">
        {done.toLocaleString()} / {total.toLocaleString()} benchmark points
      </p>
    </div>
  );
}

function SelectedValue({ label, value }: { label: string; value: number | null }) {
  if (value === null) return null;
  return (
    <p className="text-sm text-muted-foreground">
      {label}: <span className="font-medium text-foreground">{value.toLocaleString()}</span>
    </p>
  );
}

export function ProfilerWorkflow({ controller }: { controller: ProfilerController }) {
  const job = controller.job;
  if (!job) return null;

  const downloadStep = job.steps[0];
  const benchmarkStep = job.steps[1];
  const configureStepBase = job.steps[2];
  if (!downloadStep || !benchmarkStep || !configureStepBase) return null;

  const configurationChosen = job.selected_max_num_seqs !== null;
  const oomRecoveryOptions = job.awaiting_oom_recovery ? job.oom_recovery : null;
  const readyToConfigure = job.awaiting_deploy_config || configurationChosen;
  const benchmarkStatus: ProfilerStep["status"] = readyToConfigure
    ? "done"
    : benchmarkStep.status === "pending" && job.status === "running"
      ? "running"
      : benchmarkStep.status;

  // Every path that surfaces the final step — the deploy-config form, or the OOM-recovery/restart
  // prompt — has stopped the sweep, so the benchmark card must stop spinning. A green check means the
  // whole sweep finished cleanly; an orange warning means it stopped short (capacity failure, a skip,
  // a timeout, or a capped context length).
  const benchmarkResolved = readyToConfigure || oomRecoveryOptions !== null;
  const benchmarkWentWell =
    !job.awaiting_oom_recovery &&
    !job.benchmark_skipped &&
    job.benchmark_timeout_num_seqs === null &&
    job.context_length_warnings.length === 0;
  const benchmarkIcon: IconStatus =
    benchmarkStep.status === "error"
      ? "error"
      : benchmarkResolved
        ? benchmarkWentWell
          ? "done"
          : "warning"
        : benchmarkStatus;
  const configureStep: ProfilerStep = {
    ...configureStepBase,
    status: configurationChosen ? "done" : configureStepBase.status === "error" ? "error" : "running",
    detail: configurationChosen
      ? ""
      : "Use the charts below to pick the best values for your use case. All listed values are safe to use and won't crash vLLM."
  };
  const configureIcon: IconStatus = configurationChosen
    ? "done"
    : configureStepBase.status === "error"
      ? "error"
      : "editing";

  return (
    // Keep chronological DOM order for assistive technology while showing the current step first.
    <div className="flex flex-col-reverse gap-3">
      <StepCard title={downloadStep.title} step={downloadStep} />

      {downloadStep.status === "done" ? (
        <StepCard
          action={
            // Skipping drops every --max-num-seqs still to be benchmarked and jumps to the deploy choice,
            // so the backend only offers it once one of them has been benchmarked end to end.
            benchmarkStep.status === "running" && !readyToConfigure && job.benchmark_skippable ? (
              <Button
                disabled={controller.skipPending || job.benchmark_skipped}
                onClick={controller.skipBenchmark}
                size="sm"
                type="button"
                variant="outline"
              >
                {controller.skipPending || job.benchmark_skipped ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <SkipForward className="h-3.5 w-3.5" />
                )}
                Skip
              </Button>
            ) : null
          }
          icon={benchmarkIcon}
          showDetail={false}
          step={{ ...benchmarkStep, status: benchmarkStatus }}
          title={benchmarkStep.title}
        >
          <BenchmarkProgress done={job.benchmark_progress_done} total={job.benchmark_progress_total} />
          {benchmarkStep.detail ? (
            // While the sweep is paused on a capacity failure, its detail *is* the failure — show it as one.
            oomRecoveryOptions ? (
              <WarningNotice>{benchmarkStep.detail}</WarningNotice>
            ) : (
              <p className="text-sm text-muted-foreground">{benchmarkStep.detail}</p>
            )
          ) : null}
          <ContextLengthWarnings warnings={job.context_length_warnings} />
          {controller.skipError ? <p className="text-xs text-destructive">{controller.skipError}</p> : null}
          <BenchCharts
            points={job.bench_points}
            running={job.status === "running"}
            selectedMaxModelLen={job.selected_max_model_len}
            stressTests={job.stress_tests}
          />
        </StepCard>
      ) : null}

      {/* Estimates only make sense once the sweep has stopped adding points — and the place the user
          wants them is right where the deploy values get picked. flex-col-reverse puts this between
          the benchmark card and the configuration card. */}
      {readyToConfigure && job.bench_points.length > 0 ? <BenchCalculator points={job.bench_points} /> : null}

      {readyToConfigure || oomRecoveryOptions ? (
        <StepCard icon={configureIcon} step={configureStep} title={configureStepBase.title}>
          {oomRecoveryOptions ? (
            <OomRecoveryForm
              disabled={controller.oomRecoveryPending}
              error={controller.oomRecoveryError}
              onDeploy={controller.deployOomRecovery}
              onRetry={controller.retryOomRecovery}
              onSaveAndRetry={controller.saveAndRetryOomRecovery}
              options={oomRecoveryOptions}
              pendingAction={controller.oomRecoveryPendingAction}
            />
          ) : !configurationChosen && job.kv_token_size && job.server_max_model_len ? (
            <DeployConfigForm
              defaultMaxNumSeqs={Math.max(1, ...job.benchmarked_max_num_seqs_values)}
              disabled={controller.choosePending}
              error={controller.chooseError}
              onChoose={controller.chooseDeployConfig}
              serverMaxModelLen={job.server_max_model_len}
            />
          ) : null}
          <SelectedValue label="Selected --max-num-seqs" value={job.selected_max_num_seqs} />
          <SelectedValue label="Selected --max-model-len" value={job.selected_max_model_len} />
          {job.status === "error" && job.error ? (
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-md border border-destructive/40 bg-background p-3 text-xs text-destructive">
              {job.error}
            </pre>
          ) : null}
          {job.status === "done" ? (
            <p className="flex items-center gap-2 text-sm text-primary">
              Profile saved.
            </p>
          ) : null}
        </StepCard>
      ) : null}
    </div>
  );
}
