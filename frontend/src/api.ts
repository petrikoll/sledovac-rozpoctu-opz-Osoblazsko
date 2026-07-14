export const AUTH_EXPIRED_EVENT = "opz-auth-expired";

const TRANSIENT_STATUSES = new Set([408, 429, 502, 503, 504]);
const WAKE_RETRY_DELAYS = [1500, 3000, 6000];

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
    this.name = "ApiError";
  }
}

function expireAuthentication() {
  localStorage.removeItem("opz_google_token");
  sessionStorage.setItem("opz_auth_expired", "1");
  window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
}

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function authenticatedFetch(path: string, init?: RequestInit) {
  const method = (init?.method || "GET").toUpperCase();
  const canRetry = method === "GET" || method === "HEAD";
  let attempt = 0;

  while (true) {
    const token = localStorage.getItem("opz_google_token");
    try {
      const response = await fetch("/api" + path, {
        ...init,
        headers: {
          ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...init?.headers,
        },
      });
      if (response.status === 401) expireAuthentication();
      if (canRetry && TRANSIENT_STATUSES.has(response.status) && attempt < WAKE_RETRY_DELAYS.length) {
        await sleep(WAKE_RETRY_DELAYS[attempt++]);
        continue;
      }
      return response;
    } catch (error) {
      if (!canRetry || attempt >= WAKE_RETRY_DELAYS.length) throw error;
      await sleep(WAKE_RETRY_DELAYS[attempt++]);
    }
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await authenticatedFetch(path, init);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: "Nastala neočekávaná chyba." }));
    throw new ApiError(payload.detail, response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

export async function downloadApi(path: string, fileName: string, init?: RequestInit) {
  const response = await authenticatedFetch(path, init);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: "Soubor se nepodařilo vytvořit." }));
    throw new ApiError(payload.detail, response.status);
  }
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export const czk = new Intl.NumberFormat("cs-CZ", { style: "currency", currency: "CZK" });
export const pct = new Intl.NumberFormat("cs-CZ", { maximumFractionDigits: 1 });
