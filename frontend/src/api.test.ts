import { afterEach, expect, test, vi } from "vitest";
import { api, AUTH_EXPIRED_EVENT } from "./api";

afterEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

test("odpověď 204 po odstranění nečte jako JSON", async () => {
  const json = vi.fn();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 204, json }));

  await expect(api("/projects/test/lump-sum-spending/test", { method: "DELETE" })).resolves.toBeUndefined();

  expect(json).not.toHaveBeenCalled();
});

test("při odpovědi 401 odstraní neplatné přihlášení", async () => {
  localStorage.setItem("opz_google_token", "expired-token");
  const expired = vi.fn();
  window.addEventListener(AUTH_EXPIRED_EVENT, expired, { once: true });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: false,
    status: 401,
    json: async () => ({ detail: "Google přihlášení není platné." }),
  }));

  await expect(api("/projects")).rejects.toThrow("Google přihlášení není platné.");

  expect(localStorage.getItem("opz_google_token")).toBeNull();
  expect(sessionStorage.getItem("opz_auth_expired")).toBe("1");
  expect(expired).toHaveBeenCalledOnce();
});

test("po dočasné chybě probouzeného serveru zopakuje bezpečné načtení", async () => {
  vi.useFakeTimers();
  const fetchMock = vi.fn()
    .mockResolvedValueOnce({ ok: false, status: 503 })
    .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ([{ project_id: "1" }]) });
  vi.stubGlobal("fetch", fetchMock);

  const result = api<{ project_id: string }[]>("/projects");
  await vi.advanceTimersByTimeAsync(1500);

  await expect(result).resolves.toEqual([{ project_id: "1" }]);
  expect(fetchMock).toHaveBeenCalledTimes(2);
});
