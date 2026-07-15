export const API_BASE = "";

const API_KEY_STORAGE_KEY = "coloma.api-key";
export const UNAUTHORIZED_EVENT = "coloma:unauthorized";

export function notifyUnauthorized() {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  }
}

export function getStoredApiKey(): string {
  try {
    return window.localStorage.getItem(API_KEY_STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setStoredApiKey(key: string) {
  try {
    if (key) {
      window.localStorage.setItem(API_KEY_STORAGE_KEY, key);
    } else {
      window.localStorage.removeItem(API_KEY_STORAGE_KEY);
    }
  } catch {
    // Storage unavailable (private mode); the key just won't persist.
  }
}

export function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const key = getStoredApiKey();
  if (key) {
    headers.set("X-API-Key", key);
  }
  return fetch(`${API_BASE}${path}`, { ...init, headers });
}

export class UnauthorizedError extends Error {
  constructor() {
    super("Unauthorized: enter the dashboard API key to continue.");
    this.name = "UnauthorizedError";
  }
}

export async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    if (response.status === 401) {
      notifyUnauthorized();
      throw new UnauthorizedError();
    }
    const text = await response.text();
    let message = text || `Request failed with ${response.status}`;
    try {
      const parsed = JSON.parse(text) as { detail?: unknown };
      if (typeof parsed.detail === "string" && parsed.detail) {
        message = parsed.detail;
      }
    } catch {
      // Response body wasn't JSON; fall back to the raw text.
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}
