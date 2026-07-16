import { Check, Copy, Loader2, Pencil, Play, Square, Terminal, Trash2 } from "lucide-react";
import { useCopyToClipboard } from "../../lib/clipboard";
import type { ProfilerArtifact, ProfilerArtifactSummary } from "../../types";
import { Button } from "../ui/button";
import { InfoNotice } from "../ui/info-notice";
import type { DeploymentController } from "./useDeploymentController";

function ProfileSelector({
  profiles,
  profilesPending,
  selectedArtifact,
  selectedProfileId,
  disabled,
  onChange,
}: {
  profiles: ProfilerArtifactSummary[];
  profilesPending: boolean;
  selectedArtifact: ProfilerArtifact | undefined;
  selectedProfileId: number | null;
  disabled: boolean;
  onChange: (id: number | null) => void;
}) {
  return (
    <>
      <label className="sr-only" htmlFor="deploy-profile">
        Saved profile
      </label>
      <select
        className="h-9 min-w-0 w-full rounded-md border border-input bg-background px-2 text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60 sm:w-80"
        disabled={disabled || profilesPending || profiles.length === 0}
        id="deploy-profile"
        onChange={(event) => onChange(event.target.value ? Number(event.target.value) : null)}
        value={selectedProfileId ?? ""}
      >
        <option value="">
          {profilesPending
            ? "Loading saved profiles…"
            : profiles.length === 0
              ? "No saved profiles"
              : "Select a saved profile…"}
        </option>
        {selectedArtifact && !profiles.some((profile) => profile.id === selectedArtifact.id) ? (
          <option value={selectedArtifact.id}>{selectedArtifact.model_name}</option>
        ) : null}
        {profiles.map((profile) => (
          <option key={profile.id} value={profile.id}>
            {profile.model_name} — {new Date(profile.created_at).toLocaleString()}
          </option>
        ))}
      </select>
    </>
  );
}

function DeploymentActions({ controller }: { controller: DeploymentController }) {
  if (controller.runtimeActive) {
    return (
      <Button disabled={controller.stopDisabled} onClick={controller.stop} type="button" variant="outline">
        {controller.stopPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Square className="h-4 w-4" />}
        Stop
      </Button>
    );
  }

  return (
    <>
      {controller.selectedArtifact ? (
        <Button
          disabled={controller.deletePending}
          onClick={controller.deleteProfile}
          size="sm"
          type="button"
          variant="destructive"
        >
          {controller.deletePending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Trash2 className="h-3.5 w-3.5" />
          )}
          Delete
        </Button>
      ) : null}
      <Button disabled={!controller.canDeploy} onClick={controller.deploy} type="button">
        {controller.startPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
        {controller.startPending ? "Deploying..." : "Deploy"}
      </Button>
    </>
  );
}

function LaunchCommand({ controller }: { controller: DeploymentController }) {
  const { status: copyStatus, copy } = useCopyToClipboard();

  if (!controller.command) return null;

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
          <Terminal className="h-3.5 w-3.5" />
          Launch command
        </div>
        {!controller.runtimeActive ? (
          <Button onClick={controller.toggleCommandEditing} size="sm" type="button" variant="ghost">
            <Pencil className="h-3.5 w-3.5" />
            {controller.isEditingCommand ? "Cancel" : "Edit"}
          </Button>
        ) : null}
      </div>
      <div className="relative">
        {controller.isEditingCommand ? (
          <textarea
            className="w-full overflow-x-auto rounded-md bg-muted p-3 pr-10 font-mono text-xs"
            onChange={(event) => controller.setEditedCommand(event.target.value)}
            rows={controller.command.split("\n").length}
            spellCheck={false}
            value={controller.editedCommand}
          />
        ) : (
          <pre className="overflow-x-auto rounded-md bg-muted p-3 pr-10 text-xs">{controller.command}</pre>
        )}
        <button
          aria-label={copyStatus === "copied" ? "Copied" : "Copy launch command"}
          className="absolute right-2 top-2 rounded-md p-1 text-muted-foreground transition-colors hover:bg-background/60 hover:text-foreground"
          onClick={() => void copy(controller.command)}
          type="button"
        >
          {copyStatus === "copied" ? (
            <Check className="h-3.5 w-3.5 text-emerald-600" />
          ) : (
            <Copy className="h-3.5 w-3.5" />
          )}
        </button>
      </div>
      {copyStatus === "error" ? (
        <p className="text-xs text-destructive">Copy failed. Select the command manually.</p>
      ) : null}
    </div>
  );
}

export function DeploymentPanel({ controller }: { controller: DeploymentController }) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
          <ProfileSelector
            disabled={controller.selectionLocked}
            onChange={controller.selectProfile}
            profiles={controller.profiles}
            profilesPending={controller.profilesPending}
            selectedArtifact={controller.selectedArtifact}
            selectedProfileId={controller.selectedProfileId}
          />
          <span aria-live="polite" className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
            {controller.deploymentStatus}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <DeploymentActions controller={controller} />
        </div>
      </div>

      {controller.profilerRunning && !controller.runtimeActive ? (
        <InfoNotice>Deploy is disabled while a profile is running.</InfoNotice>
      ) : null}
      {controller.profilesError ? <p className="text-xs text-destructive">{controller.profilesError}</p> : null}
      {controller.kvTokenSize != null ? (
        <p className="text-xs text-muted-foreground">
          KV cache size: <span className="font-medium text-foreground">{controller.kvTokenSize.toLocaleString()} tokens</span>
        </p>
      ) : null}
      {controller.selectedArtifactPending ? (
        <div className="h-20 animate-pulse rounded-md bg-muted" />
      ) : (
        <LaunchCommand controller={controller} />
      )}
      {controller.deploymentError ? <p className="text-xs text-destructive">{controller.deploymentError}</p> : null}
    </div>
  );
}
