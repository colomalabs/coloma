import { AlertTriangle, Cpu } from "lucide-react";
import { useDeployStatus } from "../../lib/queries";
import type { GpuStats } from "../../types";

function StatBar({ label, valueLabel, percent }: { label: string; valueLabel: string; percent: number }) {
  const clamped = Math.max(0, Math.min(100, percent));

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>{label}</span>
        <span className="truncate">{valueLabel}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-muted">
        <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${clamped}%` }} />
      </div>
    </div>
  );
}

function GpuCard({ gpu }: { gpu: GpuStats }) {
  const memoryPercent = gpu.memory_total_mib > 0 ? (gpu.memory_used_mib / gpu.memory_total_mib) * 100 : 0;

  return (
    <div className="space-y-3 rounded-md border bg-card px-3 py-2.5">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2 text-sm font-medium">
          <Cpu className="h-4 w-4 shrink-0 text-muted-foreground" />
          <span className="truncate">{gpu.name}</span>
        </div>
        <span className="shrink-0 text-xs text-muted-foreground">GPU {gpu.index}</span>
      </div>
      <StatBar label="Compute" percent={gpu.utilization_percent} valueLabel={`${Math.round(gpu.utilization_percent)}%`} />
      <StatBar
        label="Memory"
        percent={memoryPercent}
        valueLabel={`${Math.round(gpu.memory_used_mib).toLocaleString()} / ${Math.round(gpu.memory_total_mib).toLocaleString()} MiB`}
      />
    </div>
  );
}

export function GpuStatus() {
  const { data, error, isPending } = useDeployStatus();
  const message = data?.gpu_error || (error instanceof Error ? error.message : "");

  return (
    <div className="space-y-2">
      {isPending ? <div className="h-28 animate-pulse rounded-md border bg-muted" /> : null}
      {!isPending && message ? (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          {message}
        </p>
      ) : null}
      {!isPending && !message && data?.gpus.length === 0 ? (
        <p className="text-xs text-muted-foreground">No GPUs detected.</p>
      ) : null}
      {!isPending && !message ? (
        <div className="space-y-2">{data?.gpus.map((gpu) => <GpuCard gpu={gpu} key={gpu.index} />)}</div>
      ) : null}
    </div>
  );
}
