export function parseSseDeltaLine(rawLine: string): string | null {
  const line = rawLine.trim();
  if (!line.startsWith("data:")) {
    return null;
  }
  const data = line.slice("data:".length).trim();
  if (!data || data === "[DONE]") {
    return null;
  }
  try {
    const chunk = JSON.parse(data);
    const delta = chunk?.choices?.[0]?.delta?.content;
    return typeof delta === "string" && delta ? delta : null;
  } catch {
    return null;
  }
}

export function parseSseUsageLine(
  rawLine: string,
): { prompt_tokens: number; completion_tokens: number; total_tokens: number } | null {
  const line = rawLine.trim();
  if (!line.startsWith("data:")) {
    return null;
  }
  const data = line.slice("data:".length).trim();
  if (!data || data === "[DONE]") {
    return null;
  }
  try {
    const chunk = JSON.parse(data);
    const usage = chunk?.usage;
    if (
      usage &&
      typeof usage.prompt_tokens === "number" &&
      typeof usage.completion_tokens === "number" &&
      typeof usage.total_tokens === "number"
    ) {
      return {
        prompt_tokens: usage.prompt_tokens,
        completion_tokens: usage.completion_tokens,
        total_tokens: usage.total_tokens,
      };
    }
    return null;
  } catch {
    return null;
  }
}
