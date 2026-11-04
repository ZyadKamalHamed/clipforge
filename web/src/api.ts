// Talking to the engine: JSON in and out, each failure as one plain sentence, and a window that reloads
// itself once when the engine restarted under it (its old session cookie then gets a 401).

import type { ErrorBody } from "./types.ts";

export const OFFLINE =
  "ClipForge isn't answering. Open it again from your Applications folder, then try again.";
export const UNREADABLE =
  "ClipForge sent an answer this window can't read. Reload the window, then try again.";
export const BROKEN =
  "Something went wrong in this window. Reload the window, then try again.";
export const RELOAD_STAMP = "clipforge-reloaded-at";
const RELOAD_GAP_MS = 10_000;

// The cache's keys, so the pages and the event stream name each thing the same way.
export const key = {
  settings: ["settings"],
  jobs: ["jobs"],
  activeJobs: ["jobs", "active"],
  projects: ["projects"],
  project: (id: string) => ["project", id],
  clips: (projectId: string) => ["clips", projectId],
  clip: (id: string) => ["clip", id],
  gameplay: ["library", "gameplay"],
  starter: ["library", "starter"],
  music: ["library", "music"],
  sfx: ["library", "sfx"],
  fonts: ["library", "fonts"],
  doctor: ["doctor"],
  storage: ["storage"],
  service: ["service"],
  accounts: ["accounts"],
  posts: ["posts"],
  needsYou: ["posts", "needs_you"],
  allClips: ["clips"],
  anyClip: ["clip"],
  drafts: ["drafts"],
  channels: ["clip-channels"],
  campaigns: ["campaigns"],
  money: ["campaigns", "money"], // refreshed with the campaigns
  briefs: ["briefs"],
  briefSources: ["brief-sources"],
  advice: ["advice"],
  campaign: (id: string) => ["campaign", id],
  today: ["today"],
  trends: ["trends"],
  // the sounds suggested for a clip, for one account's posts or for any (refreshed with the trends)
  clipSounds: (clipId: string, platform?: string, accountId?: string) => [
    "trends",
    "clip",
    clipId,
    platform ?? "",
    accountId ?? "",
  ],
} as const;

export class ApiError extends Error {
  status: number;
  code: string;
  field: string | null;
  body: unknown; // the whole answer, for a refusal that carries more (a changed plan, say)

  constructor(
    status: number,
    code: string,
    message: string,
    field: string | null = null,
    body: unknown = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.field = field;
    this.body = body;
  }
}

// Reloads the window unless it reloaded itself in the last 10 s, so an engine that keeps refusing the
// cookie can't put it in a loop. Without session storage there's no telling, so it never reloads.
export function reloadOnce(now: number = Date.now()): boolean {
  try {
    if (now - Number(sessionStorage.getItem(RELOAD_STAMP) ?? 0) < RELOAD_GAP_MS)
      return false;
    sessionStorage.setItem(RELOAD_STAMP, String(now));
  } catch {
    return false;
  }
  location.reload();
  return true;
}

// What the window says about a failure: the server's own sentence, else one of its own, never a stack trace.
export function sentence(err: unknown): string {
  return err instanceof ApiError ? err.message : BROKEN;
}

function failure(status: number, body: unknown): ApiError {
  if (status === 401) reloadOnce();
  const b = body as Partial<ErrorBody> | null | undefined;
  if (b && typeof b.message === "string") {
    return new ApiError(
      status,
      typeof b.error === "string" ? b.error : "http",
      b.message,
      b.field ?? null,
      body,
    );
  }
  return new ApiError(status, "http", UNREADABLE);
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (typeof init.body === "string" && !headers.has("Content-Type"))
    headers.set("Content-Type", "application/json");
  let res: Response;
  try {
    res = await fetch(path, { ...init, headers });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, "offline", OFFLINE);
  }
  if (res.status === 204) return undefined as T;
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    throw failure(res.status, undefined);
  }
  if (!res.ok) throw failure(res.status, body);
  return body as T;
}

// A file sent as the raw request body, with how much of it has gone (0 to 1) as it goes.
export function upload<T>(
  path: string,
  file: Blob, // a File, or a take the voiceover recorder made
  headers: Record<string, string>,
  onProgress: (share: number) => void,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    for (const [name, value] of Object.entries(headers))
      xhr.setRequestHeader(name, value);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && e.total > 0) onProgress(e.loaded / e.total);
    };
    xhr.onerror = () => reject(new ApiError(0, "offline", OFFLINE));
    xhr.onload = () => {
      let body: unknown;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        body = undefined;
      }
      if (xhr.status >= 200 && xhr.status < 300 && body !== undefined)
        resolve(body as T);
      else reject(failure(xhr.status, body));
    };
    xhr.send(file);
  });
}
