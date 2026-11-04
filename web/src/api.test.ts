import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import { ApiError, BROKEN, OFFLINE, RELOAD_STAMP, UNREADABLE, api, reloadOnce, sentence } from "./api.ts";

// The window's fetch, session storage and reload, as the tests stand them in.
let reloads = 0;
let stored = new Map<string, string>();
let sent: Array<{ url: string; init: RequestInit }> = [];
let answer: () => Promise<Response> = async () => Response.json({});

function stand(name: string, value: unknown) {
  Object.defineProperty(globalThis, name, { configurable: true, value });
}

beforeEach(() => {
  reloads = 0;
  stored = new Map();
  sent = [];
  stand("sessionStorage", {
    getItem: (k: string) => stored.get(k) ?? null,
    setItem: (k: string, v: string) => void stored.set(k, String(v)),
  });
  stand("location", { reload: () => void (reloads += 1) });
  stand("fetch", (url: string, init: RequestInit) => {
    sent.push({ url, init });
    return answer();
  });
});

const failure = async (p: Promise<unknown>): Promise<ApiError> => {
  try {
    await p;
  } catch (err) {
    assert.ok(err instanceof ApiError);
    return err;
  }
  assert.fail("it should have failed");
};

test("an API error carries the server's code, sentence and field", async () => {
  answer = async () =>
    Response.json({ error: "invalid_setting", message: "Pick a theme.", field: "theme" }, { status: 422 });
  const err = await failure(api("/api/settings"));
  assert.equal(err.status, 422);
  assert.equal(err.code, "invalid_setting");
  assert.equal(err.message, "Pick a theme.");
  assert.equal(err.field, "theme");
});

test("a text body goes as JSON, and a 204 gives nothing back", async () => {
  answer = async () => new Response(null, { status: 204 });
  assert.equal(await api("/api/projects/p1", { method: "DELETE", body: JSON.stringify({ a: 1 }) }), undefined);
  assert.equal(new Headers(sent[0].init.headers).get("Content-Type"), "application/json");
  answer = async () => Response.json({ ok: true });
  assert.deepEqual(await api("/api/health"), { ok: true });
});

test("a 401 reloads the window, at most once in 10 s", async () => {
  answer = async () =>
    Response.json({ error: "session", message: "ClipForge restarted.", field: null }, { status: 401 });
  await failure(api("/api/jobs?active=1"));
  await failure(api("/api/jobs?active=1"));
  assert.equal(reloads, 1);
  const stamp = Number(stored.get(RELOAD_STAMP));
  assert.equal(reloadOnce(stamp + 9_000), false);
  assert.equal(reloadOnce(stamp + 10_001), true);
  assert.equal(reloads, 2);
});

test("without session storage the window never reloads itself, so it can't loop", () => {
  stand("sessionStorage", {
    getItem: () => {
      throw new Error("blocked");
    },
    setItem: () => {
      throw new Error("blocked");
    },
  });
  assert.equal(reloadOnce(Date.now()), false);
  assert.equal(reloads, 0);
});

test("a server that isn't answering, or an answer that isn't JSON, gives a plain sentence", async () => {
  answer = async () => {
    throw new TypeError("Failed to fetch");
  };
  const offline = await failure(api("/api/jobs"));
  assert.deepEqual([offline.status, offline.code, offline.message], [0, "offline", OFFLINE]);
  answer = async () => new Response("<html>oops</html>", { status: 500 });
  const odd = await failure(api("/api/jobs"));
  assert.deepEqual([odd.status, odd.code, odd.message], [500, "http", UNREADABLE]);
});

test("a failure shows as the server's sentence, and anything else as one plain sentence too", () => {
  assert.equal(sentence(new ApiError(422, "invalid_setting", "Pick a theme.", "theme")), "Pick a theme.");
  assert.equal(sentence(new TypeError("x is undefined")), BROKEN);
  assert.equal(sentence("odd"), BROKEN);
});
