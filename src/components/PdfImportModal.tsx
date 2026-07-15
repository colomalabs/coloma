import { useEffect, useState } from "react";
import { Check, Loader2, RefreshCw, X } from "lucide-react";
import { Button } from "./ui/button";
import { apiFetch, readJson } from "../lib/api";

const DEFAULT_DPI = 300;
const DEFAULT_QUALITY = 80;

type PdfRenderResponse = {
  images: string[];
};

type PdfPage = {
  dataUrl: string;
  selected: boolean;
};

type PdfImportModalProps = {
  file: File;
  onCancel: () => void;
  onInsert: (dataUrls: string[]) => void;
};

export function PdfImportModal({ file, onCancel, onInsert }: PdfImportModalProps) {
  const [dpi, setDpi] = useState(String(DEFAULT_DPI));
  const [blackAndWhite, setBlackAndWhite] = useState(true);
  const [quality, setQuality] = useState(String(DEFAULT_QUALITY));
  const [pages, setPages] = useState<PdfPage[]>([]);
  const [loading, setLoading] = useState(false);
  const [hasRendered, setHasRendered] = useState(false);
  const [error, setError] = useState("");

  async function render(currentDpi: number, currentBlackAndWhite: boolean, currentQuality: number) {
    setLoading(true);
    setError("");
    try {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("dpi", String(currentDpi));
      formData.append("black_and_white", String(currentBlackAndWhite));
      formData.append("quality", String(currentQuality));
      const response = await apiFetch("/api/pdf/render", { method: "POST", body: formData });
      const payload = await readJson<PdfRenderResponse>(response);
      setPages(payload.images.map((dataUrl) => ({ dataUrl, selected: true })));
      setHasRendered(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not render PDF");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void render(DEFAULT_DPI, true, DEFAULT_QUALITY);
    // Render once on open with the defaults; subsequent setting changes apply via Re-render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [file]);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onCancel();
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onCancel]);

  function togglePage(index: number) {
    setPages((current) => current.map((page, i) => (i === index ? { ...page, selected: !page.selected } : page)));
  }

  const selectedCount = pages.filter((page) => page.selected).length;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" role="presentation">
      <div aria-modal="true" className="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-md border bg-background shadow-lg" role="dialog">
        <div className="flex items-center justify-between border-b px-4 py-3">
          <h2 className="truncate text-sm font-semibold">Import pages from {file.name}</h2>
          <button
            aria-label="Close"
            className="shrink-0 rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
            onClick={onCancel}
            type="button"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex flex-wrap items-end gap-4 border-b px-4 py-3">
          <div className="grid gap-1">
            <label className="text-xs font-medium text-muted-foreground" htmlFor="pdf-dpi">
              DPI
            </label>
            <input
              className="h-9 w-24 rounded-md border border-input bg-background px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              id="pdf-dpi"
              max={600}
              min={50}
              onChange={(event) => setDpi(event.target.value)}
              type="number"
              value={dpi}
            />
          </div>
          <div className="grid gap-1">
            <label className="text-xs font-medium text-muted-foreground" htmlFor="pdf-quality">
              JPEG quality
            </label>
            <input
              className="h-9 w-24 rounded-md border border-input bg-background px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              id="pdf-quality"
              max={100}
              min={1}
              onChange={(event) => setQuality(event.target.value)}
              type="number"
              value={quality}
            />
          </div>
          <label className="flex items-center gap-2 pb-2 text-sm">
            <input
              checked={blackAndWhite}
              className="h-4 w-4 rounded border-input text-primary focus-visible:ring-2 focus-visible:ring-ring"
              onChange={(event) => setBlackAndWhite(event.target.checked)}
              type="checkbox"
            />
            Black &amp; white
          </label>
          <Button
            disabled={loading}
            onClick={() => void render(Number(dpi) || DEFAULT_DPI, blackAndWhite, Number(quality) || DEFAULT_QUALITY)}
            type="button"
            variant="outline"
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            Re-render
          </Button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {error ? <p className="pb-3 text-sm text-destructive">{error}</p> : null}
          {loading && !hasRendered ? (
            <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Rendering pages…
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4">
              {pages.map((page, index) => (
                <button
                  className={`relative overflow-hidden rounded-md border-2 text-left transition-opacity ${
                    page.selected ? "border-primary" : "border-transparent opacity-40"
                  }`}
                  key={index}
                  onClick={() => togglePage(index)}
                  type="button"
                >
                  <img alt={`Page ${index + 1}`} className="aspect-[3/4] w-full bg-muted object-cover" src={page.dataUrl} />
                  <span className="absolute bottom-1 right-1 rounded bg-background/90 px-1.5 py-0.5 text-xs">{index + 1}</span>
                  <span
                    className={`absolute left-1 top-1 flex h-5 w-5 items-center justify-center rounded-full border ${
                      page.selected ? "border-primary bg-primary text-primary-foreground" : "border-input bg-background"
                    }`}
                  >
                    {page.selected ? <Check className="h-3 w-3" /> : null}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="flex items-center justify-between border-t px-4 py-3">
          <p className="text-sm text-muted-foreground">
            {selectedCount} of {pages.length} page{pages.length === 1 ? "" : "s"} selected
          </p>
          <div className="flex gap-2">
            <Button onClick={onCancel} type="button" variant="outline">
              Cancel
            </Button>
            <Button
              disabled={!selectedCount || loading}
              onClick={() => onInsert(pages.filter((page) => page.selected).map((page) => page.dataUrl))}
              type="button"
            >
              Insert {selectedCount} page{selectedCount === 1 ? "" : "s"}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
