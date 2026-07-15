import { useEffect, useRef, useState } from "react";

export type CopyStatus = "idle" | "copied" | "error";

async function writeClipboardText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  // Fallback for insecure contexts (the dashboard is often served over plain
  // HTTP on a LAN, where navigator.clipboard is unavailable).
  const textArea = document.createElement("textarea");
  textArea.value = text;
  textArea.setAttribute("readonly", "");
  textArea.style.position = "fixed";
  textArea.style.left = "-9999px";
  document.body.appendChild(textArea);
  textArea.select();
  document.execCommand("copy");
  document.body.removeChild(textArea);
}

export function useCopyToClipboard(resetDelayMs = 2000) {
  const [status, setStatus] = useState<CopyStatus>("idle");
  const timeoutRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (timeoutRef.current !== null) {
        window.clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  const copy = async (text: string) => {
    try {
      await writeClipboardText(text);
      setStatus("copied");
    } catch {
      setStatus("error");
    }
    if (timeoutRef.current !== null) {
      window.clearTimeout(timeoutRef.current);
    }
    timeoutRef.current = window.setTimeout(() => setStatus("idle"), resetDelayMs);
  };

  return { status, copy };
}
