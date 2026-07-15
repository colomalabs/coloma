import { Fragment, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock3,
  Loader2,
  Radio,
  RefreshCw,
  XCircle,
} from "lucide-react";
import { Button } from "./ui/button";
import { CopyButton } from "./ui/copy-button";
import { apiFetch, getStoredApiKey, readJson } from "../lib/api";
import { parseSseDeltaLine } from "../lib/sse";
import type { RequestSummary, RequestsResponse } from "../types";

const REQUESTS_POLL_INTERVAL_MS = 2000;

function formatTime(timestamp: number) {
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(timestamp * 1000));
}

function formatDuration(value: number | null) {
  if (value == null) {
    return "-";
  }
  if (value < 1000) {
    return `${Math.round(value)} ms`;
  }
  return `${(value / 1000).toFixed(1)} s`;
}

function statusTone(request: RequestSummary) {
  if (request.running) {
    return "text-muted-foreground";
  }
  if (request.error || (request.status_code != null && request.status_code >= 500)) {
    return "text-destructive";
  }
  if (request.status_code != null && request.status_code >= 400) {
    return "text-amber-700";
  }
  return "text-primary";
}

function statusIcon(request: RequestSummary) {
  const className = `h-4 w-4 ${statusTone(request)}`;
  if (request.running) {
    return <Loader2 className={`${className} animate-spin`} />;
  }
  if (request.error || (request.status_code != null && request.status_code >= 500)) {
    return <XCircle className={className} />;
  }
  if (request.status_code != null && request.status_code >= 400) {
    return <AlertCircle className={className} />;
  }
  return <CheckCircle2 className={className} />;
}

function requestStatus(request: RequestSummary) {
  if (request.running) {
    return "Running";
  }
  if (request.status_code == null) {
    return request.error ? "Error" : "-";
  }
  return String(request.status_code);
}

function VerificationBadge({ request }: { request: RequestSummary }) {
  if (!request.verification_status) {
    return null;
  }
  if (request.verification_status === "queued") {
    return (
      <span title="Verification queued">
        <Clock3 aria-label="Verification queued" className="h-4 w-4 text-muted-foreground" />
      </span>
    );
  }
  if (request.verification_status === "running") {
    return (
      <span title="Verification running">
        <Loader2 aria-label="Verification running" className="h-4 w-4 animate-spin text-muted-foreground" />
      </span>
    );
  }
  if (request.verification_status === "error") {
    return (
      <span title="Verification failed to run">
        <XCircle aria-label="Verification failed to run" className="h-4 w-4 text-destructive" />
      </span>
    );
  }
  if (request.verification_resolved) {
    return (
      <span className="inline-flex items-center gap-1 whitespace-nowrap text-xs text-primary" title="Verification resolved the validation issue">
        <CheckCircle2 className="h-4 w-4" />
        Resolved
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap text-xs text-amber-700" title="Verification did not resolve the validation issue">
      <XCircle className="h-4 w-4" />
      Not resolved
    </span>
  );
}

function prettyJson(body: string) {
  try {
    return JSON.stringify(JSON.parse(body), null, 2);
  } catch {
    return body;
  }
}

function bodyPreview(body: string | null, truncated: boolean) {
  if (!body) {
    return "";
  }
  const pretty = prettyJson(body);
  return truncated ? `${pretty}\n[truncated]` : pretty;
}

function isStreamedBody(body: string | null) {
  if (!body) {
    return false;
  }
  const trimmed = body.trim();
  if (!trimmed) {
    return false;
  }
  try {
    JSON.parse(trimmed);
    return false;
  } catch {
    return trimmed.split("\n").some((line) => line.trim().startsWith("data:"));
  }
}

function extractSseText(body: string) {
  const parts: string[] = [];
  for (const rawLine of body.split("\n")) {
    const delta = parseSseDeltaLine(rawLine);
    if (delta) {
      parts.push(delta);
    }
  }
  return parts.join("");
}

function BodyPanel({ title, content }: { title: string; content: string }) {
  return (
    <div className="min-w-0 space-y-2">
      <div className="flex items-center justify-between">
        <h4 className="text-xs font-medium uppercase text-muted-foreground">{title}</h4>
        <CopyButton text={content} />
      </div>
      <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-all rounded-md bg-muted p-3 text-xs leading-relaxed text-foreground">
        {content || "-"}
      </pre>
    </div>
  );
}

// Captured bodies are fetched on demand: the list endpoint intentionally omits
// them so 2-second polling stays cheap.
function RequestDetails({ request }: { request: RequestSummary }) {
  const [showText, setShowText] = useState(false);
  const { data, isPending, error } = useQuery({
    queryKey: ["request-detail", request.request_id],
    queryFn: async ({ signal }) =>
      readJson<RequestSummary>(await apiFetch(`/api/requests/${request.request_id}`, { signal })),
    refetchInterval: request.running ? REQUESTS_POLL_INTERVAL_MS : false,
  });

  if (isPending) {
    return <div className="h-24 animate-pulse rounded-md bg-muted" />;
  }
  if (error || !data) {
    return (
      <p className="text-sm text-destructive">
        {error instanceof Error ? error.message : "Could not load request details"}
      </p>
    );
  }

  const streamed = isStreamedBody(data.response_body);
  const responseContent =
    streamed && showText
      ? extractSseText(data.response_body ?? "")
      : bodyPreview(data.response_body || data.error, data.response_truncated);

  return (
    <div className="space-y-3">
      {data.validation_issues.length > 0 ? (
        <div className="space-y-2 rounded-md border border-amber-700/30 bg-amber-700/10 p-3">
          <h4 className="flex items-center gap-2 text-xs font-medium uppercase text-amber-700">
            <AlertTriangle className="h-3.5 w-3.5" />
            Validation issues
          </h4>
          <ul className="list-inside list-disc space-y-1 text-xs text-amber-700">
            {data.validation_issues.map((issue, index) => (
              <li key={index}>{issue}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {data.verification_status === "done" && data.verification_issues.length > 0 ? (
        <div className="space-y-2 rounded-md border border-amber-700/30 bg-amber-700/10 p-3">
          <h4 className="flex items-center gap-2 text-xs font-medium uppercase text-amber-700">
            <AlertTriangle className="h-3.5 w-3.5" />
            Issues remaining after verification
          </h4>
          <ul className="list-inside list-disc space-y-1 text-xs text-amber-700">
            {data.verification_issues.map((issue, index) => (
              <li key={index}>{issue}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {data.verification_error ? (
        <p className="text-sm text-destructive">Verification error: {data.verification_error}</p>
      ) : null}
      <div className={`grid gap-3 ${data.verification_response_body ? "lg:grid-cols-4" : "lg:grid-cols-3"}`}>
        <BodyPanel content={bodyPreview(data.original_request_body, data.original_request_truncated)} title="Original request" />
        <BodyPanel content={bodyPreview(data.request_body, data.request_truncated)} title="Optimized request" />
        <div className="min-w-0 space-y-2">
          <div className="flex items-center justify-between gap-2">
            <h4 className="text-xs font-medium uppercase text-muted-foreground">Response</h4>
            <div className="flex items-center gap-1">
              {streamed ? (
                <div className="flex items-center rounded-md border p-0.5 text-xs">
                  <button
                    className={`rounded px-2 py-0.5 ${!showText ? "bg-accent text-foreground" : "text-muted-foreground"}`}
                    onClick={() => setShowText(false)}
                    type="button"
                  >
                    Raw
                  </button>
                  <button
                    className={`rounded px-2 py-0.5 ${showText ? "bg-accent text-foreground" : "text-muted-foreground"}`}
                    onClick={() => setShowText(true)}
                    type="button"
                  >
                    Text
                  </button>
                </div>
              ) : null}
              <CopyButton text={responseContent} />
            </div>
          </div>
          <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-all rounded-md bg-muted p-3 text-xs leading-relaxed text-foreground">
            {responseContent || "-"}
          </pre>
        </div>
        {data.verification_response_body ? (
          <BodyPanel content={bodyPreview(data.verification_response_body, false)} title="Verification response" />
        ) : null}
      </div>
    </div>
  );
}

function TrafficTable({ emptyText, loading, requests }: { emptyText: string; loading: boolean; requests: RequestSummary[] }) {
  const [expandedRequestId, setExpandedRequestId] = useState<string | null>(null);

  if (loading) {
    return (
      <div className="flex items-center justify-center gap-2 rounded-md border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading requests…
      </div>
    );
  }

  if (!requests.length) {
    return <div className="rounded-md border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">{emptyText}</div>;
  }

  return (
    <div className="overflow-x-auto rounded-md border">
      <table className="min-w-full border-collapse text-sm">
        <thead className="bg-muted text-left text-xs uppercase text-muted-foreground">
          <tr>
            <th className="w-10 px-3 py-2 font-medium" aria-label="Details" />
            <th className="px-3 py-2 font-medium">Status</th>
            <th className="px-3 py-2 font-medium">Time</th>
            <th className="px-3 py-2 font-medium">Method</th>
            <th className="px-3 py-2 font-medium">Path</th>
            <th className="px-3 py-2 font-medium">Model</th>
            <th className="px-3 py-2 text-right font-medium">Duration</th>
          </tr>
        </thead>
        <tbody>
          {requests.map((request) => {
            const hasIssues = request.validation_issues.length > 0;
            const hasDetails =
              request.has_original_request_body ||
              request.has_request_body ||
              request.has_response_body ||
              Boolean(request.error) ||
              hasIssues;
            const expanded = expandedRequestId === request.request_id;

            return (
              <Fragment key={request.request_id}>
                <tr className="border-t align-middle">
                  <td className="px-3 py-3">
                    <button
                      aria-label={expanded ? "Hide request details" : "Show request details"}
                      className="inline-flex h-7 w-7 items-center justify-center rounded-md hover:bg-accent disabled:opacity-30"
                      disabled={!hasDetails}
                      onClick={() => setExpandedRequestId(expanded ? null : request.request_id)}
                      type="button"
                    >
                      {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                    </button>
                  </td>
                  <td className="px-3 py-3">
                    <div className="flex items-center gap-2 whitespace-nowrap">
                      {statusIcon(request)}
                      <span className={statusTone(request)}>{requestStatus(request)}</span>
                      {hasIssues ? (
                        <span title={request.validation_issues.join("; ")}>
                          <AlertTriangle aria-label="Response validation issues" className="h-4 w-4 text-amber-700" />
                        </span>
                      ) : null}
                      <VerificationBadge request={request} />
                    </div>
                  </td>
                  <td className="whitespace-nowrap px-3 py-3 text-muted-foreground">{formatTime(request.started_at)}</td>
                  <td className="px-3 py-3 font-medium">{request.method}</td>
                  <td className="max-w-[360px] px-3 py-3">
                    <div className="truncate font-medium">{request.path}</div>
                    {request.query_string ? <div className="truncate text-xs text-muted-foreground">?{request.query_string}</div> : null}
                  </td>
                  <td className="max-w-[220px] truncate px-3 py-3 text-muted-foreground">{request.model || "-"}</td>
                  <td className="whitespace-nowrap px-3 py-3 text-right text-muted-foreground">
                    {formatDuration(request.running ? request.elapsed_ms : request.latency_ms)}
                  </td>
                </tr>
                {expanded ? (
                  <tr className="border-t bg-card">
                    <td className="px-3 py-3" />
                    <td className="px-3 py-3" colSpan={6}>
                      <RequestDetails request={request} />
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function StepNumber({ value }: { value: number }) {
  return (
    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
      {value}
    </span>
  );
}

function GettingStarted() {
  // The dashboard origin also serves the /v1 proxy (vite proxies it in dev and
  // preview), so it stays correct behind TLS or a reverse proxy.
  const baseUrl = typeof window !== "undefined" ? `${window.location.origin}/v1` : "http://localhost:8001/v1";
  const storedKey = getStoredApiKey();
  const apiKey = storedKey || "<dashboard API key>";

  const snippet = `from openai import OpenAI

client = OpenAI(
    base_url="${baseUrl}",
    api_key="${apiKey}",
)`;

  return (
    <div className="space-y-6 rounded-md border border-dashed bg-card px-6 py-8">
      <div className="flex items-start gap-3">
        <Radio className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
        <div className="space-y-1.5">
          <h3 className="text-base font-semibold">Waiting for traffic</h3>
          <p className="text-sm text-muted-foreground">
            Coloma is a reverse proxy: it sits between your application and your OpenAI-compatible backend
            (e.g. vLLM), transparently forwarding every request while capturing, validating, and optimizing it along
            the way. This tab fills in automatically as soon as traffic flows through it, nothing else to configure
            here.
          </p>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex gap-3">
          <StepNumber value={1} />
          <p className="text-sm">
            Set your upstream server's URL and key in the <span className="font-medium">Config</span> tab, that's
            where requests get forwarded to.
          </p>
        </div>
        <div className="flex gap-3">
          <StepNumber value={2} />
          <p className="text-sm">
            Point your app's OpenAI client at this proxy instead of your upstream server directly, using the
            dashboard API key.
          </p>
        </div>
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium uppercase text-muted-foreground">It's a drop-in replacement</span>
          <CopyButton text={snippet} />
        </div>
        <pre className="overflow-auto whitespace-pre rounded-md bg-muted p-3 text-xs leading-relaxed text-foreground">
          {snippet}
        </pre>
        {!storedKey ? (
          <p className="text-xs text-muted-foreground">
            Set the dashboard API key in the Config tab to fill in the snippet above.
          </p>
        ) : null}
      </div>
    </div>
  );
}

export function TrafficTab() {
  const {
    data,
    isPending,
    isFetching,
    error,
    refetch,
  } = useQuery({
    queryKey: ["requests"],
    queryFn: async ({ signal }) => readJson<RequestsResponse>(await apiFetch("/api/requests?limit=100", { signal })),
    refetchInterval: REQUESTS_POLL_INTERVAL_MS,
  });

  const active = data?.active ?? [];
  const saved = data?.saved ?? [];
  const message = error ? (error instanceof Error ? error.message : "Could not load requests") : "";

  const [onlyValidationFailures, setOnlyValidationFailures] = useState(false);

  const totalSaved = saved.length;
  const recentWithBodies = useMemo(
    () => saved.filter((request) => request.has_original_request_body || request.has_request_body || request.has_response_body),
    [saved],
  );
  const allRequests = useMemo(() => {
    // Saved wins over active on id collisions: a record is written to the tee
    // db just before it is removed from the in-flight set.
    const merged = new Map<string, RequestSummary>();
    for (const request of active) {
      merged.set(request.request_id, request);
    }
    for (const request of saved) {
      merged.set(request.request_id, request);
    }
    return Array.from(merged.values()).sort((a, b) => b.started_at - a.started_at);
  }, [active, saved]);
  const validationFailureCount = useMemo(
    () => allRequests.filter((request) => request.validation_issues.length > 0).length,
    [allRequests],
  );
  const visibleRequests = useMemo(
    () => (onlyValidationFailures ? allRequests.filter((request) => request.validation_issues.length > 0) : allRequests),
    [allRequests, onlyValidationFailures],
  );

  return (
    <section className="mx-auto w-full max-w-[1600px] px-5 py-5">
      <div className="space-y-6">
        {message ? <p className="text-sm text-destructive">{message}</p> : null}

        <div className="grid gap-3 sm:grid-cols-3">
          <div className="rounded-md border bg-card px-4 py-3">
            <div className="flex items-center gap-2 text-sm text-muted-foreground"><Clock3 className="h-4 w-4" /> Running</div>
            {!isPending ? (
              <div className="mt-2 text-2xl font-semibold">{active.length}</div>
            ) : (
              <div className="mt-2 h-8 w-10 animate-pulse rounded bg-muted" />
            )}
          </div>
          <div className="rounded-md border bg-card px-4 py-3">
            <div className="text-sm text-muted-foreground">Saved</div>
            {!isPending ? (
              <div className="mt-2 text-2xl font-semibold">{totalSaved}</div>
            ) : (
              <div className="mt-2 h-8 w-10 animate-pulse rounded bg-muted" />
            )}
          </div>
          <div className="rounded-md border bg-card px-4 py-3">
            <div className="text-sm text-muted-foreground">Captured bodies</div>
            {!isPending ? (
              <div className="mt-2 text-2xl font-semibold">{recentWithBodies.length}</div>
            ) : (
              <div className="mt-2 h-8 w-10 animate-pulse rounded bg-muted" />
            )}
          </div>
        </div>

        {!isPending && allRequests.length === 0 ? (
          <GettingStarted />
        ) : (
          <div className="space-y-3">
            <div className="flex items-center justify-between gap-2">
              <Button
                className={onlyValidationFailures ? "border-amber-700/50 text-amber-700" : ""}
                onClick={() => setOnlyValidationFailures((current) => !current)}
                type="button"
                variant={onlyValidationFailures ? "secondary" : "outline"}
              >
                <AlertTriangle className="h-4 w-4" />
                {onlyValidationFailures ? "Showing validation failures" : "Show validation failures only"}
                {validationFailureCount > 0 ? ` (${validationFailureCount})` : ""}
              </Button>
              <Button disabled={isFetching} onClick={() => void refetch()} type="button" variant="outline">
                {isFetching ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                Refresh
              </Button>
            </div>
            <TrafficTable
              emptyText={
                onlyValidationFailures
                  ? "No requests with validation issues."
                  : "No requests yet. Send traffic through /v1/* to populate this list."
              }
              loading={isPending}
              requests={visibleRequests}
            />
          </div>
        )}
      </div>
    </section>
  );
}
