import { useMemo, useState, type MouseEvent } from "react";
import { Info } from "lucide-react";

const CHART_WIDTH = 520;
// The extra bottom padding (and the height compensating for it, keeping the plot area unchanged)
// fits the tilted x tick labels plus the axis label under them.
const CHART_HEIGHT = 272;
const CHART_PAD = { top: 16, right: 20, bottom: 52, left: 60 };

// On a linear x-axis the points can bunch (1 next to 1k, the longest sweep prompt next to the
// full-context stress point). Tilting alone cannot save two anchors a few pixels apart — the
// rotated labels just overlap in parallel — so a tick landing closer than this to the previously
// labelled one keeps its point but loses its label; the tooltip still names it.
const MIN_TICK_GAP = 18;

function niceTicks(max: number, count = 4): number[] {
  if (max <= 0) return [0];
  const rawStep = max / count;
  const magnitude = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= rawStep) ?? rawStep;
  const top = Math.ceil(max / step) * step;
  const ticks: number[] = [];
  for (let value = 0; value <= top + step * 0.001; value += step) ticks.push(value);
  return ticks;
}

function logTicks(min: number, max: number): number[] {
  const ticks: number[] = [];
  for (let exp = Math.floor(Math.log10(min)); exp <= Math.ceil(Math.log10(max)); exp += 1) ticks.push(Math.pow(10, exp));
  return ticks;
}

export type ChartSeries<T> = {
  // Stable identity of the series, used as its React key. Defaults to the label, which is fine when
  // labels are distinct (prompt lengths); pass it explicitly whenever two series can carry the same
  // label — duplicate keys make React reuse the wrong line, drawing it in the other series' color.
  id?: string;
  label: string;
  color: string;
  value: (point: T) => number;
  // Optional extra detail shown next to the value in the hover card (e.g. the prompt length,
  // which varies per point).
  hint?: (point: T) => string;
};

// Hover/focus target explaining what the chart's metric measures. Kept as plain text (not a rich
// popover) so it can also be the icon's accessible name.
export function MetricInfo({ description }: { description: string }) {
  return (
    <span className="group relative inline-flex">
      <Info
        aria-label={description}
        className="h-3.5 w-3.5 cursor-help text-muted-foreground/60 hover:text-foreground"
        role="img"
        tabIndex={0}
      />
      <span
        className="pointer-events-none absolute left-1/2 top-5 z-20 hidden w-64 -translate-x-1/2 rounded-md border bg-card p-2 text-xs font-normal leading-relaxed text-foreground shadow-md group-hover:block group-focus-within:block"
        role="tooltip"
      >
        {description}
      </span>
    </span>
  );
}

export type SeriesChartProps<T> = {
  title: string;
  // What the metric means, surfaced through an info icon next to the title.
  description?: string;
  unit: string;
  format: (value: number) => string;
  points: T[];
  series: ChartSeries<T>[];
  logScale: boolean;
  // How x positions are spaced: "log" suits an axis that grows by doubling (concurrency),
  // "linear" keeps true proportions (prompt tokens).
  xScale?: "linear" | "log";
  xValue: (point: T) => number;
  xTickLabel: (point: T) => string;
  xAxisLabel: string;
  tooltipTitle: (point: T) => string;
  pointKey: (point: T) => string | number;
};

export function SeriesChart<T>({
  title,
  description,
  unit,
  format,
  points,
  series,
  logScale,
  xScale = "log",
  xValue,
  xTickLabel,
  xAxisLabel,
  tooltipTitle,
  pointKey,
}: SeriesChartProps<T>) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  const { xFor, yFor, yTicks, seriesValues, labelledTicks } = useMemo(() => {
    const seriesValues = series.map((s) => points.map(s.value));
    const plotWidth = CHART_WIDTH - CHART_PAD.left - CHART_PAD.right;
    const plotHeight = CHART_HEIGHT - CHART_PAD.top - CHART_PAD.bottom;

    let yTicks: number[];
    let yFor: (value: number) => number;
    const allValues = seriesValues.flat();
    const positiveValues = allValues.filter((v) => Number.isFinite(v) && v > 0);
    if (logScale && positiveValues.length > 0) {
      yTicks = logTicks(Math.min(...positiveValues), Math.max(...positiveValues));
      const lo = Math.log10(yTicks[0]);
      const span = Math.max(Math.log10(yTicks[yTicks.length - 1]) - lo, 1e-9);
      yFor = (value) => CHART_PAD.top + plotHeight - ((Math.log10(Math.max(value, yTicks[0])) - lo) / span) * plotHeight;
    } else {
      const yMax = Math.max(...allValues.filter(Number.isFinite), 0) * 1.1 || 1;
      yTicks = niceTicks(yMax);
      const yTop = yTicks[yTicks.length - 1];
      yFor = (value) => CHART_PAD.top + plotHeight - (value / yTop) * plotHeight;
    }

    const xs = points.map((p) => (xScale === "linear" ? xValue(p) : Math.log2(Math.max(xValue(p), 1))));
    const xMin = xs[0] ?? 0;
    const xSpan = Math.max((xs[xs.length - 1] ?? 0) - xMin, 1e-9);
    const xFor = (index: number) =>
      points.length === 1 ? CHART_PAD.left + plotWidth / 2 : CHART_PAD.left + ((xs[index] - xMin) / xSpan) * plotWidth;

    // Assumes the caller's points arrive sorted by x, which both chart views guarantee.
    const labelledTicks = new Set<number>();
    let lastLabelX = Number.NEGATIVE_INFINITY;
    for (let index = 0; index < points.length; index += 1) {
      if (xFor(index) - lastLabelX >= MIN_TICK_GAP) {
        labelledTicks.add(index);
        lastLabelX = xFor(index);
      }
    }

    return { xFor, yFor, yTicks, seriesValues, labelledTicks };
  }, [points, series, logScale, xScale, xValue]);

  const onMove = (event: MouseEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = ((event.clientX - rect.left) / rect.width) * CHART_WIDTH;
    let best = 0;
    for (let i = 1; i < points.length; i += 1) {
      if (Math.abs(xFor(i) - x) < Math.abs(xFor(best) - x)) best = i;
    }
    setHoverIndex(best);
  };

  return (
    <div className="relative min-w-0">
      <div className="mb-1 flex items-center gap-1.5">
        <h4 className="text-xs font-medium text-muted-foreground">{title}</h4>
        {description ? <MetricInfo description={description} /> : null}
      </div>
      <svg
        viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
        className="w-full"
        role="img"
        aria-label={title}
        onMouseMove={onMove}
        onMouseLeave={() => setHoverIndex(null)}
      >
        {yTicks.map((tick) => (
          <g key={tick}>
            <line x1={CHART_PAD.left} x2={CHART_WIDTH - CHART_PAD.right} y1={yFor(tick)} y2={yFor(tick)} stroke="hsl(214 16% 90%)" strokeWidth={1} />
            <text x={CHART_PAD.left - 8} y={yFor(tick) + 3} textAnchor="end" className="fill-current text-muted-foreground" fontSize={12}>
              {format(tick)}
            </text>
          </g>
        ))}
        {points.map((point, index) =>
          labelledTicks.has(index) ? (
            <text
              key={pointKey(point)}
              x={xFor(index)}
              y={CHART_HEIGHT - CHART_PAD.bottom + 16}
              textAnchor="end"
              transform={`rotate(-35 ${xFor(index)} ${CHART_HEIGHT - CHART_PAD.bottom + 16})`}
              className="fill-current text-muted-foreground"
              fontSize={12}
            >
              {xTickLabel(point)}
            </text>
          ) : null,
        )}
        <text
          x={(CHART_PAD.left + CHART_WIDTH - CHART_PAD.right) / 2}
          y={CHART_HEIGHT - 4}
          textAnchor="middle"
          className="fill-current text-muted-foreground"
          fontSize={14}
        >
          {xAxisLabel}
        </text>

        {hoverIndex !== null ? (
          <line
            x1={xFor(hoverIndex)}
            x2={xFor(hoverIndex)}
            y1={CHART_PAD.top}
            y2={CHART_HEIGHT - CHART_PAD.bottom}
            stroke="hsl(220 9% 60%)"
            strokeWidth={1}
            strokeDasharray="3 3"
          />
        ) : null}

        {series.map((s, seriesIndex) => {
          const values = seriesValues[seriesIndex];
          const plottable = values
            .map((value, index) => ({ value, index }))
            .filter(({ value }) => Number.isFinite(value));
          return (
            <g key={s.id ?? s.label}>
              {plottable.length > 1 ? (
                <polyline
                  points={plottable.map(({ value, index }) => `${xFor(index)},${yFor(value)}`).join(" ")}
                  fill="none"
                  stroke={s.color}
                  strokeWidth={2}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              ) : null}
              {plottable.map(({ value, index }) => (
                <circle
                  key={pointKey(points[index])}
                  cx={xFor(index)}
                  cy={yFor(value)}
                  r={hoverIndex === index ? 5 : 4}
                  fill={s.color}
                  stroke="hsl(0 0% 100%)"
                  strokeWidth={2}
                />
              ))}
            </g>
          );
        })}
      </svg>

      {hoverIndex !== null ? (
        <div
          className="pointer-events-none absolute top-8 z-10 rounded-md border bg-card px-2.5 py-1.5 text-xs shadow-sm"
          style={
            xFor(hoverIndex) < CHART_WIDTH / 2
              ? { left: `calc(${(xFor(hoverIndex) / CHART_WIDTH) * 100}% + 8px)` }
              : { right: `calc(${((CHART_WIDTH - xFor(hoverIndex)) / CHART_WIDTH) * 100}% + 8px)` }
          }
        >
          <div className="font-medium">{tooltipTitle(points[hoverIndex])}</div>
          {series.map((s, seriesIndex) => {
            const value = seriesValues[seriesIndex][hoverIndex];
            const hint = Number.isFinite(value) ? s.hint?.(points[hoverIndex]) : "";
            return (
              <div key={s.id ?? s.label} className="mt-0.5 flex items-center gap-1.5 whitespace-nowrap text-muted-foreground">
                {series.length > 1 ? <span className="h-2 w-2 rounded-full" style={{ background: s.color }} /> : null}
                {series.length > 1 ? `${s.label}: ` : `${title}: `}
                <span className="font-mono text-foreground">
                  {Number.isFinite(value) ? `${format(value)}${unit}` : "—"}
                </span>
                {hint ? <span className="text-muted-foreground/80">({hint})</span> : null}
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
