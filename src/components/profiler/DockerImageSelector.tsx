import { useEffect, type Dispatch, type SetStateAction } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Download, Loader2 } from "lucide-react";
import { apiFetch, readJson } from "../../lib/api";
import { DEPLOY_STATUS_QUERY_KEY } from "../../lib/queries";
import type { DeployStatusResponse, DockerPullStatus } from "../../types";
import { Button } from "../ui/button";

function versionPrefix(image: string): string {
  const tag = image.split(":").pop() ?? "";
  return tag.split(".").slice(0, 2).join(".");
}

function matchesTargetVersion(image: string, targetImage: string): boolean {
  return versionPrefix(image).startsWith(versionPrefix(targetImage));
}

export function DockerImageSelector({
  images,
  pullStatus,
  selectedImage,
  setSelectedImage,
  disabled,
  targetImage,
}: {
  images: string[];
  pullStatus: DockerPullStatus;
  selectedImage: string;
  setSelectedImage: Dispatch<SetStateAction<string>>;
  disabled: boolean;
  targetImage: string;
}) {
  const queryClient = useQueryClient();

  useEffect(() => {
    setSelectedImage((current) => {
      if (current && images.includes(current)) return current;
      return images.find((image) => matchesTargetVersion(image, targetImage)) ?? images[0] ?? "";
    });
  }, [images, targetImage, setSelectedImage]);

  const pullMutation = useMutation({
    mutationFn: async (image: string) =>
      readJson<DockerPullStatus>(
        await apiFetch("/api/deploy/docker/pull", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ image }),
        }),
      ),
    // The pull runs in the background and the response already reports it as running. Publish that
    // rather than waiting for the next status poll, which would let the button fall back to "Pull"
    // for a round-trip after the mutation settles.
    onSuccess: (docker_pull) => {
      queryClient.setQueryData<DeployStatusResponse>(DEPLOY_STATUS_QUERY_KEY, (previous) =>
        previous ? { ...previous, docker_pull } : previous,
      );
      void queryClient.invalidateQueries({ queryKey: DEPLOY_STATUS_QUERY_KEY });
    },
  });

  const targetTag = targetImage.split(":").pop() ?? targetImage;
  const hasTargetVersion = images.some((image) => matchesTargetVersion(image, targetImage));
  const pulling = pullMutation.isPending || pullStatus.state === "running";
  const showVersionWarning = selectedImage !== "" && !matchesTargetVersion(selectedImage, targetImage);

  return (
    <div className="space-y-2">
      <label className="text-sm font-medium" htmlFor="vllm-docker-image">
        vLLM Docker image
      </label>
      <div className="flex gap-2">
        <select
          className="h-10 min-w-0 flex-1 rounded-md border border-input bg-background px-3 text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          disabled={disabled || images.length === 0}
          id="vllm-docker-image"
          onChange={(event) => setSelectedImage(event.target.value)}
          value={selectedImage}
        >
          {images.length === 0 ? (
            <option value="">No vLLM images found</option>
          ) : (
            images.map((image) => (
              <option key={image} value={image}>
                {image}
              </option>
            ))
          )}
        </select>
        {!hasTargetVersion ? (
          <Button
            disabled={disabled || pulling}
            onClick={() => pullMutation.mutate(targetImage)}
            type="button"
            variant="outline"
          >
            {pulling ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
            {pulling ? "Pulling…" : `Pull ${targetTag}`}
          </Button>
        ) : null}
      </div>
      {showVersionWarning ? (
        <p className="flex items-center gap-2 text-xs text-amber-600">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          This profiler was built for {targetImage}, {selectedImage} may not work as expected.
        </p>
      ) : null}
      {pullStatus.state === "error" ? (
        <p className="text-xs text-destructive">
          Pull of {pullStatus.image} failed: {pullStatus.error}
        </p>
      ) : null}
    </div>
  );
}
