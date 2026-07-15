import { Check, Copy } from "lucide-react";
import { useCopyToClipboard } from "../../lib/clipboard";

export function CopyButton({ text }: { text: string }) {
  const { status, copy } = useCopyToClipboard();

  if (!text) {
    return null;
  }

  return (
    <button
      aria-label="Copy to clipboard"
      className="inline-flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
      onClick={() => void copy(text)}
      type="button"
    >
      {status === "copied" ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
    </button>
  );
}
