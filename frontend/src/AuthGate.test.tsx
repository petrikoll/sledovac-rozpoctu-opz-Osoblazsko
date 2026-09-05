import { useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, test, vi } from "vitest";
import { AuthGate } from "./App";
import { AUTH_EXPIRED_EVENT } from "./api";

const credential = (email: string) => `header.${btoa(JSON.stringify({ email, exp: Math.floor(Date.now() / 1000) + 3600 }))}.signature`;
function Draft() {
  const [value, setValue] = useState("");
  return <input aria-label="Rozpracovaný údaj" value={value} onChange={e => setValue(e.target.value)} />;
}
afterEach(() => { cleanup(); localStorage.clear(); sessionStorage.clear(); vi.unstubAllGlobals(); });

test.each([true, false])("rozpracovaný formulář přežije expiraci jen pro stejný účet (%s)", async sameAccount => {
  localStorage.setItem("opz_google_token", credential("first@example.invalid"));
  const qc = new QueryClient();
  qc.setQueryData(["private"], "cached data");
  let login: ((response: { credential: string }) => void) | undefined;
  vi.stubGlobal("google", { accounts: { id: {
    initialize: ({ callback }: { callback: typeof login }) => { login = callback; },
    renderButton: vi.fn(),
  } } });
  render(<QueryClientProvider client={qc}><AuthGate><Draft /></AuthGate></QueryClientProvider>);
  fireEvent.change(screen.getByLabelText("Rozpracovaný údaj"), { target: { value: "12345" } });
  act(() => { localStorage.removeItem("opz_google_token"); window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT)); });
  expect(screen.getByLabelText("Rozpracovaný údaj")).not.toBeVisible();
  await waitFor(() => expect(login).toBeDefined());
  act(() => login!({ credential: credential(sameAccount ? "first@example.invalid" : "second@example.invalid") }));
  expect(screen.getByLabelText("Rozpracovaný údaj")).toBeVisible();
  expect(screen.getByLabelText("Rozpracovaný údaj")).toHaveValue(sameAccount ? "12345" : "");
  expect(qc.getQueryData(["private"])).toBe(sameAccount ? "cached data" : undefined);
});
