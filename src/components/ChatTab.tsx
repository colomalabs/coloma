import { useEffect, useMemo, useRef, useState, type DragEvent, type KeyboardEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { Paperclip, Send, Square, SquarePen, X } from "lucide-react";
import { Button } from "./ui/button";
import { PdfImportModal } from "./PdfImportModal";
import { apiFetch, notifyUnauthorized, readJson, UnauthorizedError } from "../lib/api";
import { useAppConfig } from "../lib/queries";
import { SCHEMA_TYPES } from "../lib/schema";
import { parseSseDeltaLine, parseSseUsageLine } from "../lib/sse";
import type { ModelsResponse, SchemaField } from "../types";

const CHAT_TEMPERATURE = 0;
const JSON_SCHEMA_TYPES = new Set<string>(SCHEMA_TYPES);

type ContentPart = { type: "text"; text: string } | { type: "image_url"; image_url: { url: string } };

// "error" messages are shown in the transcript but never sent to the API.
type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "error";
  text: string;
  images: string[];
  usage?: TokenUsage;
};

type TokenUsage = {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
};

function buildJsonSchema(fields: SchemaField[]) {
  const properties: Record<string, { type: string }> = {};
  for (const field of fields) {
    if (!field.name) {
      continue;
    }
    properties[field.name] = { type: JSON_SCHEMA_TYPES.has(field.type) ? field.type : "string" };
  }
  return {
    type: "object",
    properties,
    required: Object.keys(properties),
    additionalProperties: false,
  };
}

function toApiContent(message: ChatMessage): string | ContentPart[] {
  if (!message.images.length) {
    return message.text;
  }
  const parts: ContentPart[] = [];
  if (message.text.trim()) {
    parts.push({ type: "text", text: message.text });
  }
  for (const url of message.images) {
    parts.push({ type: "image_url", image_url: { url } });
  }
  return parts;
}

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error ?? new Error("Could not read file"));
    reader.readAsDataURL(file);
  });
}

function createId(): string {
  return typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
}

function ChatBubble({ message, onImageClick }: { message: ChatMessage; onImageClick: (url: string) => void }) {
  const isUser = message.role === "user";
  const isError = message.role === "error";
  const tone = isError
    ? "border border-destructive/40 bg-destructive/10 text-destructive"
    : isUser
      ? "bg-primary text-primary-foreground"
      : "bg-muted text-foreground";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[75%] space-y-2 rounded-md px-3 py-2 text-sm whitespace-pre-wrap break-words ${tone}`}
      >
        {message.images.length ? (
          <div className="flex flex-wrap gap-2">
            {message.images.map((url, index) => (
              <button
                aria-label="Expand image"
                className="cursor-zoom-in"
                key={index}
                onClick={() => onImageClick(url)}
                type="button"
              >
                <img alt="" className="h-24 w-24 rounded-md object-cover" src={url} />
              </button>
            ))}
          </div>
        ) : null}
        {message.text || (message.role === "assistant" ? "…" : "")}
      </div>
    </div>
  );
}

function ImageLightbox({ url, onClose }: { url: string; onClose: () => void }) {
  useEffect(() => {
    function handleKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4"
      onClick={onClose}
      role="presentation"
    >
      <button
        aria-label="Close"
        className="absolute right-4 top-4 rounded-md p-1 text-white/80 hover:bg-white/10 hover:text-white"
        onClick={onClose}
        type="button"
      >
        <X className="h-6 w-6" />
      </button>
      <img alt="" className="max-h-full max-w-full rounded-md object-contain" onClick={(event) => event.stopPropagation()} src={url} />
    </div>
  );
}

export function ChatTab() {
  const modelsQuery = useQuery({
    queryKey: ["models"],
    queryFn: async () => readJson<ModelsResponse>(await apiFetch("/api/models")),
    staleTime: 60_000,
  });

  const configQuery = useAppConfig();

  const [model, setModel] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [pendingImages, setPendingImages] = useState<string[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [structuredOutput, setStructuredOutput] = useState(false);
  const [pdfFile, setPdfFile] = useState<File | null>(null);
  const [expandedImage, setExpandedImage] = useState<string | null>(null);
  const [isDraggingFile, setIsDraggingFile] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const modelManuallySelectedRef = useRef(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const dragCounterRef = useRef(0);

  const latestUsage =
    [...messages].reverse().find((message) => message.usage)?.usage ?? {
      prompt_tokens: 0,
      completion_tokens: 0,
      total_tokens: 0,
    };

  const models = useMemo(() => modelsQuery.data?.models ?? [], [modelsQuery.data?.models]);
  const schemaFields = configQuery.data?.app_config.validation.fields ?? [];
  const isEmpty = messages.length === 0;

  useEffect(() => {
    if (!models.length) {
      modelManuallySelectedRef.current = false;
      setModel("");
      return;
    }
    if (!model || !models.includes(model) || !modelManuallySelectedRef.current) {
      modelManuallySelectedRef.current = false;
      setModel(models[0]);
    }
  }, [models, model]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  function pushError(text: string) {
    setMessages((current) => [...current, { id: createId(), role: "error", text, images: [] }]);
  }

  async function handleFilesSelected(files: FileList | null) {
    if (!files?.length) {
      return;
    }
    const selected = Array.from(files);
    const pdf = selected.find((file) => file.type === "application/pdf");
    const images = selected.filter((file) => file.type.startsWith("image/"));
    if (pdf) {
      setPdfFile(pdf);
    }
    if (images.length) {
      try {
        const urls = await Promise.all(images.map(readFileAsDataUrl));
        setPendingImages((current) => [...current, ...urls]);
      } catch (err) {
        pushError(err instanceof Error ? err.message : "Could not read the selected image");
      }
    }
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  }

  function insertPdfPages(dataUrls: string[]) {
    setPendingImages((current) => [...current, ...dataUrls]);
    setPdfFile(null);
  }

  function removePendingImage(index: number) {
    setPendingImages((current) => current.filter((_, i) => i !== index));
  }

  function handleDragEnter(event: DragEvent<HTMLDivElement>) {
    if (!event.dataTransfer.types.includes("Files")) {
      return;
    }
    event.preventDefault();
    dragCounterRef.current += 1;
    setIsDraggingFile(true);
  }

  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    if (!event.dataTransfer.types.includes("Files")) {
      return;
    }
    event.preventDefault();
  }

  function handleDragLeave(event: DragEvent<HTMLDivElement>) {
    if (!event.dataTransfer.types.includes("Files")) {
      return;
    }
    event.preventDefault();
    dragCounterRef.current = Math.max(0, dragCounterRef.current - 1);
    if (dragCounterRef.current === 0) {
      setIsDraggingFile(false);
    }
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    dragCounterRef.current = 0;
    setIsDraggingFile(false);
    void handleFilesSelected(event.dataTransfer.files);
  }

  function clearConversation() {
    abortRef.current?.abort();
    setMessages([]);
    setInput("");
    setPendingImages([]);
    setIsStreaming(false);
  }

  function stopStreaming() {
    abortRef.current?.abort();
  }

  async function sendMessage() {
    const text = input.trim();
    if (isStreaming || (!text && !pendingImages.length)) {
      return;
    }
    if (!model) {
      pushError("No models available. Check the endpoint configuration in the Config tab.");
      return;
    }

    const userMessage: ChatMessage = { id: createId(), role: "user", text, images: pendingImages };
    const history = [...messages, userMessage];
    const assistantId = createId();
    setMessages([...history, { id: assistantId, role: "assistant", text: "", images: [] }]);
    setInput("");
    setPendingImages([]);
    setIsStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;

    const useStructuredOutput = structuredOutput && schemaFields.length > 0;

    try {
      const response = await apiFetch("/v1/chat/completions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          model,
          stream: true,
          stream_options: { include_usage: true },
          temperature: CHAT_TEMPERATURE,
          messages: history
            .filter((message) => message.role !== "error")
            .map((message) => ({ role: message.role, content: toApiContent(message) })),
          ...(useStructuredOutput
            ? {
                response_format: {
                  type: "json_schema",
                  json_schema: { name: "response", strict: true, schema: buildJsonSchema(schemaFields) },
                },
              }
            : {}),
        }),
      });

      if (!response.ok || !response.body) {
        if (response.status === 401) {
          notifyUnauthorized();
          throw new UnauthorizedError();
        }
        const bodyText = await response.text();
        throw new Error(bodyText || `Request failed with ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop() ?? "";

        for (const event of events) {
          for (const line of event.split("\n")) {
            const delta = parseSseDeltaLine(line);
            if (delta) {
              setMessages((current) =>
                current.map((message) => (message.id === assistantId ? { ...message, text: message.text + delta } : message)),
              );
            }
            const usage = parseSseUsageLine(line);
            if (usage) {
              setMessages((current) =>
                current.map((message) => (message.id === assistantId ? { ...message, usage } : message)),
              );
            }
          }
        }
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        // User-initiated stop; keep whatever content streamed in so far.
      } else {
        const message = err instanceof Error ? err.message : "Chat request failed";
        setMessages((current) => [
          // Drop the assistant placeholder when nothing streamed in; keep it (marked failed) otherwise.
          ...current.filter((m) => m.id !== assistantId || m.text),
          { id: createId(), role: "error", text: message, images: [] },
        ]);
      }
    } finally {
      setIsStreaming(false);
      abortRef.current = null;
    }
  }

  function handleInputKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void sendMessage();
    }
  }

  return (
    <section
      className="relative mx-auto flex h-[calc(100dvh-3.5rem)] w-full max-w-[1600px] flex-col px-5 py-5 md:h-dvh"
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      {isDraggingFile ? (
        <div className="pointer-events-none absolute inset-5 z-10 flex items-center justify-center rounded-md border-2 border-dashed border-primary bg-background/90 text-sm font-medium text-primary">
          Drop images or a PDF to attach
        </div>
      ) : null}
      <div className="flex shrink-0 items-center justify-between gap-2 pb-3">
        <div className="flex items-center gap-4">
          <label
            className="flex items-center gap-2 text-sm text-muted-foreground has-[:disabled]:opacity-50"
            title={schemaFields.length ? "" : "Define a response validation schema in the Config tab first"}
          >
            <input
              checked={structuredOutput}
              className="h-4 w-4 rounded border-input text-primary focus-visible:ring-2 focus-visible:ring-ring"
              disabled={!schemaFields.length}
              onChange={(event) => setStructuredOutput(event.target.checked)}
              type="checkbox"
            />
            Structured outputs (from Proxy settings)
          </label>
        </div>
        <div className="flex items-center gap-4">
          <span className="text-xs text-muted-foreground">
            {latestUsage.prompt_tokens.toLocaleString()} prompt · {latestUsage.completion_tokens.toLocaleString()} completion ·{" "}
            {latestUsage.total_tokens.toLocaleString()} total tokens
          </span>
          <Button disabled={!messages.length} onClick={clearConversation} type="button" variant="outline">
            <SquarePen className="h-4 w-4" />
            New chat
          </Button>
        </div>
      </div>

      {isEmpty ? (
        <div className="flex flex-1 flex-col items-center justify-end pb-6">
          <h2 className="text-2xl font-semibold tracking-tight">What are we testing today?</h2>
        </div>
      ) : (
        <div className="mx-auto min-h-0 w-full max-w-[760px] flex-1 space-y-3 overflow-y-auto py-4" ref={scrollRef}>
          {messages.map((message) => (
            <ChatBubble key={message.id} message={message} onImageClick={setExpandedImage} />
          ))}
        </div>
      )}

      {pendingImages.length ? (
        <div className="mx-auto flex w-full max-w-[760px] shrink-0 flex-wrap gap-2 pt-3">
          {pendingImages.map((url, index) => (
            <div className="relative" key={index}>
              <img alt="" className="h-16 w-16 rounded-md border object-cover" src={url} />
              <button
                aria-label="Remove image"
                className="absolute -right-1.5 -top-1.5 inline-flex h-5 w-5 items-center justify-center rounded-full border bg-background text-muted-foreground hover:text-foreground"
                onClick={() => removePendingImage(index)}
                type="button"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
          ))}
        </div>
      ) : null}

      <div className="mx-auto mt-3 flex w-full max-w-[760px] shrink-0 items-end gap-2 rounded-2xl border border-input bg-background p-2 focus-within:ring-2 focus-within:ring-ring">
        <input
          accept="image/*,application/pdf"
          className="hidden"
          multiple
          onChange={(event) => void handleFilesSelected(event.target.files)}
          ref={fileInputRef}
          type="file"
        />
        <Button
          aria-label="Attach image or PDF"
          className="shrink-0 rounded-full text-muted-foreground hover:text-foreground"
          onClick={() => fileInputRef.current?.click()}
          size="icon"
          type="button"
          variant="ghost"
        >
          <Paperclip className="h-4 w-4" />
        </Button>
        <textarea
          className="max-h-40 min-h-9 flex-1 resize-none bg-transparent py-2 text-sm outline-none placeholder:text-muted-foreground"
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={handleInputKeyDown}
          placeholder="Send a message"
          rows={1}
          value={input}
        />
        <select
          aria-label="Model"
          className="h-9 max-w-[220px] shrink-0 cursor-pointer truncate rounded-full bg-transparent px-2 text-sm text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          disabled={modelsQuery.isPending || !models.length}
          onChange={(event) => {
            modelManuallySelectedRef.current = true;
            setModel(event.target.value);
          }}
          value={model}
        >
          {models.length ? (
            models.map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))
          ) : (
            <option value="">{modelsQuery.isPending ? "Loading models…" : "No models available"}</option>
          )}
        </select>
        {isStreaming ? (
          <Button
            aria-label="Stop"
            className="shrink-0 rounded-full"
            onClick={stopStreaming}
            size="icon"
            type="button"
            variant="outline"
          >
            <Square className="h-4 w-4" />
          </Button>
        ) : (
          <Button
            aria-label="Send"
            className="shrink-0 rounded-full"
            disabled={!model || (!input.trim() && !pendingImages.length)}
            onClick={() => void sendMessage()}
            size="icon"
            type="button"
          >
            <Send className="h-4 w-4" />
          </Button>
        )}
      </div>

      {isEmpty ? <div className="flex-1" /> : null}

      {pdfFile ? <PdfImportModal file={pdfFile} onCancel={() => setPdfFile(null)} onInsert={insertPdfPages} /> : null}
      {expandedImage ? <ImageLightbox onClose={() => setExpandedImage(null)} url={expandedImage} /> : null}
    </section>
  );
}
