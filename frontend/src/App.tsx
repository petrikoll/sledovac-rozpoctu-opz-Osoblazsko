import { useEffect, useRef, useState } from "react";
import { Link, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { api, AUTH_EXPIRED_EVENT, czk, downloadApi, pct } from "./api";
type Project = {
  project_id: string;
  project_code: string;
  project_name: string;
  recipient_name: string;
  total_budget: number;
  total_monitoring_periods: number;
  active_budget_version_id: string | null;
  status: string;
};
type Payment = {
  payment_request_id: string;
  sequence_number: number;
  request_number: string;
  request_version: number;
  is_advance_payment: boolean;
  is_final_payment: boolean;
  approved_direct_costs: number;
  approved_lump_sum: number;
  approved_total: number;
  public_payment: number;
  source_file_name: string;
};
type LumpEntry = {
  lump_sum_entry_id: string;
  monitoring_period: string;
  entry_date: string;
  entry_mode: "period" | "cumulative";
  entered_amount: number;
  note: string;
};
type CofinancingEntry = {
  cofinancing_entry_id: string;
  entry_date: string;
  amount: number;
  note: string;
};
type CofinancingStatus = {
  target: number;
  secured: number;
  remaining: number;
  percentage: number;
  entries: CofinancingEntry[];
};
type BudgetRow = {
  code: string;
  name: string;
  level: number;
  is_leaf: boolean;
  is_new: boolean;
  previous_amount: number | null;
  has_budget_change: boolean;
  change_note: string;
  category: string;
  total_amount: number;
  cumulative_spent: number;
  remaining: number;
  spent_percent: number;
  planned_future_spending: number;
  expected_final_remaining: number;
  periods: Record<string, number>;
};
type BudgetVersion = {
  version_id: string;
  file_name: string;
  total_amount: number;
};
type CurrentUser = { email: string; role: string };
type Sd2Entry = { sd2_entry_id?: string; monitoring_period: number; month: string; budget_item_code: string; gross_wage: number; employer_contributions: number; other_with_contributions: number; other_without_contributions: number; payment_date?: string | null; external_id?: string; subject_id?: string; last_name?: string; first_name?: string; employment_type?: "Smlouva" | "DPC" | "DPP" | "DPPDo" | "DPPNad" | null; work_time_fund?: number; project_hours?: number; description?: string };
type WorkerAssignment = { budget_item_code: string; employee_names: string; employee_name?: string; project_fte?: number | null; payroll_component_amount?: number | null; contract_contains?: string };
type WorkerRule = { employee_name: string; project_fte: string; payroll_component_amount: string; contract_contains: string };
type PayrollRow = { source_key: string; page_number: number; full_name: string; last_name: string; first_name: string; subject_id?: string; category: string; contract_name?: string; position_name?: string; component_code?: string; component_name?: string; component_description?: string; component_amount?: number; other_with_contributions?: number; project_bonus_available?: number; project_bonus_label?: string; employer_contribution_rate?: number; total_fte?: number; vacation_days?: number; vacation_hours?: number; project_vacation_hours?: number; month: string; gross_wage: number; employer_contributions: number; work_time_fund: number; worked_hours: number; project_hours?: number; project_fte?: number; employment_type: Sd2Entry["employment_type"]; budget_item_code: string; match_status: "matched" | "unmatched" | "ignored" };
type PayrollPreview = { file_name: string; period: number; rows: PayrollRow[]; budget_items: { code: string; name: string }[] };
type ProjectSchedule = { project_start_date: string | null; project_end_date: string | null; periods: { monitoring_period: number; start_month: string; end_month: string }[] };
const CLIENT_ID =
  import.meta.env.VITE_GOOGLE_CLIENT_ID ||
  "812727560459-codfb0fu10agboif0lsjce3k6on4rj3d.apps.googleusercontent.com";
const DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file";
const SD2_DRIVE_FOLDER = "Dokumenty aplikace OPZ+";

function monthStart(value: string) { return value ? `${value.slice(0, 7)}-01` : ""; }
function addMonths(value: string, count: number) { const date = new Date(`${monthStart(value)}T00:00:00Z`); date.setUTCMonth(date.getUTCMonth() + count); return date.toISOString().slice(0, 10); }
function monthCountInclusive(start: string, end: string) { if (!start || !end) return 0; const first = new Date(`${monthStart(start)}T00:00:00Z`); const last = new Date(`${monthStart(end)}T00:00:00Z`); return (last.getUTCFullYear() - first.getUTCFullYear()) * 12 + last.getUTCMonth() - first.getUTCMonth() + 1; }
function monthsInRange(start: string, end: string) { const count = monthCountInclusive(start, end); return count > 0 ? Array.from({ length: count }, (_, index) => addMonths(start, index)) : []; }
function monthLabel(value: string) { return value ? new Date(`${monthStart(value)}T00:00:00Z`).toLocaleDateString("cs-CZ", { month: "long", year: "numeric" }) : "nenastaveno"; }

async function googleDriveError(response: Response) {
  const payload = await response.json().catch(() => null);
  return payload?.error?.message || "Google Drive operaci odmítl.";
}

function requestDriveAccessToken() {
  return new Promise<string>((resolve, reject) => {
    if (!window.google?.accounts?.oauth2) {
      reject(new Error("Google přihlášení se ještě nenačetlo. Obnovte stránku a zkuste to znovu."));
      return;
    }
    const client = window.google.accounts.oauth2.initTokenClient({
      client_id: CLIENT_ID,
      scope: DRIVE_SCOPE,
      callback: (response: { access_token?: string; error?: string; error_description?: string }) => {
        if (response.access_token) resolve(response.access_token);
        else reject(new Error(response.error_description || response.error || "Nepodařilo se získat oprávnění k Disku."));
      },
    });
    client.requestAccessToken({ prompt: "consent", hint: tokenEmail(localStorage.getItem("opz_google_token") || "") });
  });
}

async function ensureSd2DriveFolder(accessToken: string) {
  const query = encodeURIComponent(`mimeType = 'application/vnd.google-apps.folder' and name = '${SD2_DRIVE_FOLDER}' and trashed = false`);
  const list = await fetch(`https://www.googleapis.com/drive/v3/files?q=${query}&spaces=drive&fields=files(id,name)&pageSize=10`, { headers: { Authorization: `Bearer ${accessToken}` } });
  if (!list.ok) throw new Error(await googleDriveError(list));
  const folders = await list.json() as { files?: { id: string }[] };
  if (folders.files?.[0]?.id) return folders.files[0].id;
  const created = await fetch("https://www.googleapis.com/drive/v3/files?fields=id", {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json" },
    body: JSON.stringify({ name: SD2_DRIVE_FOLDER, mimeType: "application/vnd.google-apps.folder" }),
  });
  if (!created.ok) throw new Error(await googleDriveError(created));
  return (await created.json() as { id: string }).id;
}

async function uploadSd2ArchiveToUserDrive(file: File, projectId: string, period: number, accessToken: string) {
  const folderId = await ensureSd2DriveFolder(accessToken);
  const metadata = new Blob([JSON.stringify({
    name: `SD2_${projectId}_${period}_${file.name}`,
    parents: [folderId],
    mimeType: file.type || "application/octet-stream",
  })], { type: "application/json" });
  const form = new FormData();
  form.append("metadata", metadata);
  form.append("file", file);
  const response = await fetch("https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id", {
    method: "POST", headers: { Authorization: `Bearer ${accessToken}` }, body: form,
  });
  if (!response.ok) throw new Error(await googleDriveError(response));
  return (await response.json() as { id: string }).id;
}
function tokenEmail(token: string) {
  try {
    return (
      JSON.parse(
        atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")),
      ).email || ""
    );
  } catch {
    return "";
  }
}

function tokenExpiresAt(token: string) {
  try {
    const payload = JSON.parse(
      atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")),
    );
    return typeof payload.exp === "number" ? payload.exp * 1000 : null;
  } catch {
    return null;
  }
}

function validStoredToken() {
  const token = localStorage.getItem("opz_google_token");
  const expiresAt = token ? tokenExpiresAt(token) : null;
  if (token && expiresAt !== null && expiresAt <= Date.now()) {
    localStorage.removeItem("opz_google_token");
    sessionStorage.setItem("opz_auth_expired", "1");
    return null;
  }
  return token;
}

function AuthGate({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState(validStoredToken);
  const [expired, setExpired] = useState(
    () => sessionStorage.getItem("opz_auth_expired") === "1",
  );
  const button = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const authenticationExpired = () => {
      setExpired(true);
      setToken(null);
    };
    const storageChanged = (event: StorageEvent) => {
      if (event.key === "opz_google_token") setToken(validStoredToken());
    };
    window.addEventListener(AUTH_EXPIRED_EVENT, authenticationExpired);
    window.addEventListener("storage", storageChanged);
    return () => {
      window.removeEventListener(AUTH_EXPIRED_EVENT, authenticationExpired);
      window.removeEventListener("storage", storageChanged);
    };
  }, []);
  useEffect(() => {
    if (!token) return;
    const expiresAt = tokenExpiresAt(token);
    if (expiresAt === null) return;
    const remaining = expiresAt - Date.now();
    if (remaining <= 0) {
      localStorage.removeItem("opz_google_token");
      sessionStorage.setItem("opz_auth_expired", "1");
      window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
      return;
    }
    const timer = window.setTimeout(() => {
      localStorage.removeItem("opz_google_token");
      sessionStorage.setItem("opz_auth_expired", "1");
      window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
    }, remaining);
    return () => window.clearTimeout(timer);
  }, [token]);
  useEffect(() => {
    if (token) return;
    let tries = 0;
    const timer = setInterval(() => {
      tries++;
      if (window.google?.accounts?.id && button.current) {
        clearInterval(timer);
        window.google.accounts.id.initialize({
          client_id: CLIENT_ID,
          callback: (response: { credential: string }) => {
            localStorage.setItem("opz_google_token", response.credential);
            sessionStorage.removeItem("opz_auth_expired");
            setExpired(false);
            setToken(response.credential);
          },
        });
        window.google.accounts.id.renderButton(button.current, {
          theme: "outline",
          size: "large",
          text: "signin_with",
          locale: "cs",
        });
      } else if (tries > 50) clearInterval(timer);
    }, 100);
    return () => clearInterval(timer);
  }, [token]);
  if (!token)
    return (
      <main className="login">
        <section>
          <small>ZABEZPEČENÁ INTERNÍ APLIKACE</small>
          <h1>Sledovač rozpočtu OPZ+</h1>
          <p>
            Přihlaste se povoleným Google účtem. Aplikace nezískává přístup k
            vaší e-mailové schránce.
          </p>
          {expired && (
            <p className="auth-expired-notice">
              Platnost přihlášení skončila. Pro pokračování se prosím znovu přihlaste.
            </p>
          )}
          <div ref={button} />
        </section>
      </main>
    );
  return <>{children}</>;
}
const Nav = () => {
  const token = localStorage.getItem("opz_google_token");
  const email = token ? tokenEmail(token) : "";
  return (
    <header>
      <Link to="/" className="brand">
        OPZ+ <span>Sledovač rozpočtu Osoblažsko</span>
      </Link>
      <nav>
        <Link to="/">Projekty</Link>
        <Link to="/novy">Nový projekt</Link>
        {email && (
          <button
            className="logout"
            onClick={() => {
              localStorage.removeItem("opz_google_token");
              location.reload();
            }}
          >
            Odhlásit {email}
          </button>
        )}
      </nav>
    </header>
  );
};
function InfoTip({ text }: { text: string }) {
  return (
    <span
      className="info-tip"
      tabIndex={0}
      role="img"
      aria-label={`Vysvětlení: ${text}`}
      data-tooltip={text}
      title={text}
    >
      i
    </span>
  );
}
function Projects() {
  const {
    data = [],
    isLoading,
    error,
  } = useQuery({
    queryKey: ["projects"],
    queryFn: () => api<Project[]>("/projects"),
  });
  return (
    <main>
      <div className="project-create">
        <Link className="button" to="/novy">
          + Založit projekt
        </Link>
      </div>
      {isLoading ? (
        <p>Načítám…</p>
      ) : error ? (
        <div className="alert">Data se nepodařilo načíst.</div>
      ) : data.length === 0 ? (
        <section className="empty">
          <h2>Zatím tu není žádný projekt</h2>
          <p>Založte projekt a nahrajte původní rozpočet XLSX.</p>
          <Link className="button" to="/novy">
            Založit první projekt
          </Link>
        </section>
      ) : (
        <section className="grid">
          {data.map((p) => (
            <Link
              className="project"
              to={`/projekty/${p.project_id}`}
              key={p.project_id}
            >
              <small>{p.project_code}</small>
              <h2>{p.project_name}</h2>
              <p>{p.recipient_name}</p>
              <strong>{czk.format(p.total_budget)}</strong>
              <span className="badge">{p.status}</span>
            </Link>
          ))}
        </section>
      )}
    </main>
  );
}
function NewProject() {
  const nav = useNavigate();
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm();
  const mutation = useMutation({
    mutationFn: (v: any) =>
      api<Project>("/projects", {
        method: "POST",
        body: JSON.stringify({
          ...v,
          lump_sum_rate: Number(v.lump_sum_rate) / 100,
          public_funding_rate: Number(v.public_funding_rate) / 100,
          total_monitoring_periods: Number(v.total_monitoring_periods),
        }),
      }),
    onSuccess: (p) => nav(`/projekty/${p.project_id}`),
  });
  return (
    <main>
      <small>NOVÝ PROJEKT</small>
      <h1>Založení projektu</h1>
      <form onSubmit={handleSubmit((v) => mutation.mutate(v))}>
        <label>
          Název projektu
          <input {...register("project_name", { required: true })} />
          {errors.project_name && <i>Povinné pole</i>}
        </label>
        <label>
          Registrační číslo
          <input {...register("project_code", { required: true })} />
        </label>
        <label>
          Příjemce
          <input {...register("recipient_name", { required: true })} />
        </label>
        <div className="cols">
          <label>
            Typ financování
            <select {...register("financing_type")}>
              <option value="ex-ante">Ex-ante</option>
            </select>
          </label>
          <label>
            Sazba paušálu (%)
            <input
              type="number"
              defaultValue="40"
              {...register("lump_sum_rate")}
            />
          </label>
          <label>
            Kód základu paušálu
            <input defaultValue="1.1" {...register("lump_sum_base_code")} />
          </label>
          <label>
            Veřejné financování (%)
            <input
              type="number"
              defaultValue="95"
              {...register("public_funding_rate")}
            />
          </label>
          <label>
            Počet období
            <input
              type="number"
              defaultValue="1"
              {...register("total_monitoring_periods")}
            />
          </label>
        </div>
        {mutation.error && (
          <div className="alert">{mutation.error.message}</div>
        )}
        <button disabled={mutation.isPending}>Založit projekt</button>
      </form>
    </main>
  );
}
function ImportBudget({
  id,
  compact = false,
}: {
  id: string;
  compact?: boolean;
}) {
  const [preview, setPreview] = useState<any>();
  const qc = useQueryClient();
  async function analyze(f: File) {
    const fd = new FormData();
    fd.append("file", f);
    setPreview(
      await api(`/projects/${id}/budgets/analyze`, {
        method: "POST",
        body: fd,
      }),
    );
  }
  async function confirm() {
    await api(`/projects/${id}/budgets/import`, {
      method: "POST",
      body: JSON.stringify({ token: preview.token }),
    });
    setPreview(null);
    qc.invalidateQueries({ queryKey: ["dashboard", id] });
  }
  const content = (
    <>
      <label className="upload-button">
        {compact ? "Nahrát rozpočet" : "Vybrat rozpočet"}
        <input
          aria-label="Soubor rozpočtu"
          type="file"
          accept=".xlsx"
          onChange={(e) => e.target.files?.[0] && analyze(e.target.files[0])}
        />
      </label>
      {preview && (
        <div className="preview budget-tool-preview">
          <h3>Kontrolní náhled rozpočtu</h3>
          <dl>
            <dt>Nalezené řádky</dt>
            <dd>{preview.items.length}</dd>
            <dt>Celkový rozpočet</dt>
            <dd>{czk.format(preview.total_amount)}</dd>
            <dt>Sazba paušálu</dt>
            <dd>{pct.format(preview.lump_sum_rate * 100)} %</dd>
            <dt>Koncové / souhrnné položky</dt>
            <dd>
              {preview.leaf_count} / {preview.summary_count}
            </dd>
          </dl>
          {preview.warnings?.map((x: string) => (
            <p className="warning" key={x}>
              {x}
            </p>
          ))}
          <button onClick={confirm}>Potvrdit import</button>
        </div>
      )}
    </>
  );
  return compact ? (
    <div className="budget-tool">{content}</div>
  ) : (
    <section className="panel">
      <h2>Import rozpočtu</h2>
      <p>
        Nahrajte export XLSX. Soubor nejprve analyzujeme a import potvrdíte až
        po kontrole.
      </p>
      {content}
    </section>
  );
}
function PaymentRequests({ id }: { id: string }) {
  const [preview, setPreview] = useState<any>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const qc = useQueryClient();
  const payments = useQuery({
    queryKey: ["payments", id],
    queryFn: () => api<Payment[]>(`/projects/${id}/payment-requests`),
  });
  async function analyze(file: File) {
    setError("");
    setPreview(null);
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      setPreview(
        await api(`/projects/${id}/payment-requests/analyze`, {
          method: "POST",
          body: fd,
        }),
      );
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "PDF se nepodařilo analyzovat.",
      );
    } finally {
      setBusy(false);
    }
  }
  async function confirm() {
    setError("");
    setBusy(true);
    try {
      await api(`/projects/${id}/payment-requests/import`, {
        method: "POST",
        body: JSON.stringify({ token: preview.token }),
      });
      setPreview(null);
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["payments", id] }),
        qc.invalidateQueries({ queryKey: ["dashboard", id] }),
        qc.invalidateQueries({ queryKey: ["final-settlement", id] }),
      ]);
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "ŽoP se nepodařilo importovat.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel">
      <h2>Žádosti o platbu</h2>
      <p>
        Nahrávej PDF postupně podle pořadového čísla. Každé nejprve zkontroluj a
        pak potvrď.
      </p>
      <input
        aria-label="PDF žádosti o platbu"
        type="file"
        accept=".pdf,application/pdf"
        disabled={busy}
        onChange={(e) => e.target.files?.[0] && analyze(e.target.files[0])}
      />
      {busy && <p>Analyzuji dokument…</p>}
      {error && <div className="alert">{error}</div>}
      {preview && (
        <div className="preview">
          <h3>Kontrolní náhled ŽoP č. {preview.sequence_number}</h3>
          <dl>
            <dt>Soubor</dt>
            <dd>{preview.source_file_name}</dd>
            <dt>Verze ŽoP</dt>
            <dd>{preview.request_version}</dd>
            <dt>Typ</dt>
            <dd>
              {preview.is_advance_payment ? "Úvodní záloha" : "Vyúčtování"}
            </dd>
            <dt>Schválené přímé výdaje</dt>
            <dd>{czk.format(preview.approved_direct_costs)}</dd>
            <dt>Schválený paušál</dt>
            <dd>{czk.format(preview.approved_lump_sum)}</dd>
            <dt>Schváleno celkem</dt>
            <dd>{czk.format(preview.approved_total)}</dd>
            <dt>Platba poskytovatele</dt>
            <dd>{czk.format(preview.public_payment)}</dd>
          </dl>
          {preview.is_advance_payment && (
            <p className="info">
              Toto je úvodní záloha. Do čerpání rozpočtu se nezapočítá.
            </p>
          )}
          <div className="actions">
            <button onClick={confirm} disabled={busy}>
              Potvrdit import ŽoP
            </button>
            <button
              className="secondary"
              onClick={() => setPreview(null)}
              disabled={busy}
            >
              Zrušit
            </button>
          </div>
        </div>
      )}
      {payments.data && payments.data.length > 0 && (
        <div className="payment-list">
          <h3>Importované ŽoP</h3>
          {payments.data.map((x) => (
            <article key={x.payment_request_id}>
              <div>
                <strong>ŽoP č. {x.sequence_number}</strong>
                <small>
                  {x.source_file_name} · verze {x.request_version}
                </small>
              </div>
              <span className={x.is_advance_payment ? "badge" : "amount"}>
                {x.is_advance_payment
                  ? "Úvodní záloha"
                  : czk.format(x.approved_total)}
              </span>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
function CofinancingFunding({ id }: { id: string }) {
  const qc = useQueryClient();
  const [error, setError] = useState("");
  const { data } = useQuery({
    queryKey: ["cofinancing", id],
    queryFn: () => api<CofinancingStatus>(`/projects/${id}/cofinancing`),
  });
  const {
    register,
    handleSubmit,
    reset,
    formState: { isSubmitting },
  } = useForm<{
    entry_date: string;
    amount: number;
    note: string;
  }>({
    defaultValues: {
      entry_date: new Date().toISOString().slice(0, 10),
      amount: 0,
      note: "",
    },
  });
  async function refresh() {
    await qc.invalidateQueries({ queryKey: ["cofinancing", id] });
  }
  async function save(value: {
    entry_date: string;
    amount: number;
    note: string;
  }) {
    setError("");
    try {
      await api(`/projects/${id}/cofinancing`, {
        method: "POST",
        body: JSON.stringify({ ...value, amount: Number(value.amount) }),
      });
      reset({ entry_date: value.entry_date, amount: 0, note: "" });
      await refresh();
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Prostředky se nepodařilo uložit.",
      );
    }
  }
  async function edit(entry: CofinancingEntry) {
    const value = prompt("Nová částka v Kč:", String(entry.amount));
    if (value === null) return;
    const amount = Number(value.replace(",", "."));
    if (!Number.isFinite(amount) || amount <= 0) {
      setError("Zadejte kladnou částku.");
      return;
    }
    const note = prompt("Poznámka:", entry.note ?? "");
    if (note === null) return;
    await api(`/projects/${id}/cofinancing/${entry.cofinancing_entry_id}`, {
      method: "PATCH",
      body: JSON.stringify({ amount, note }),
    });
    await refresh();
  }
  async function remove(entry: CofinancingEntry) {
    if (!confirm("Opravdu odstranit tento záznam spolufinancování?")) return;
    await api(`/projects/${id}/cofinancing/${entry.cofinancing_entry_id}`, {
      method: "DELETE",
    });
    await refresh();
  }
  const complete = Number(data?.remaining ?? 1) <= 0;
  return (
    <section
      className={`panel funding-card ${complete ? "funding-complete" : ""}`}
    >
      <h2>Zajištěné spolufinancování</h2>
      <InfoTip text="Sleduje skutečně zajištěné prostředky proti minimálnímu vlastnímu podílu z celkových přímých výdajů. Paušální nepřímé náklady se do minima nezapočítávají." />
      <div className="funding-summary">
        <div>
          <small>POTŘEBNÉ MINIMUM</small>
          <strong>{czk.format(data?.target ?? 0)}</strong>
        </div>
        <div>
          <small>JIŽ ZAJIŠTĚNO</small>
          <strong>{czk.format(data?.secured ?? 0)}</strong>
        </div>
        <div className={complete ? "funding-done" : "funding-remaining"}>
          <small>{complete ? "SPLNĚNO" : "ZBÝVÁ ZAJISTIT"}</small>
          <strong>{complete ? "✓" : czk.format(data?.remaining ?? 0)}</strong>
        </div>
      </div>
      <div
        className="funding-progress"
        aria-label={`Zajištěno ${Math.min(100, Number(data?.percentage ?? 0)).toFixed(1)} procent`}
      >
        <span
          style={{ width: `${Math.min(100, Number(data?.percentage ?? 0))}%` }}
        />
      </div>
      {complete && <p>Minimální spolufinancování je zajištěno.</p>}
      <form className="inline-form" onSubmit={handleSubmit(save)}>
        <div className="cols">
          <label>
            Datum získání
            <input
              type="date"
              {...register("entry_date", { required: true })}
            />
          </label>
          <label>
            Částka
            <input
              type="number"
              step="0.01"
              min="0.01"
              {...register("amount", { required: true, valueAsNumber: true })}
            />
          </label>
        </div>
        <div className="funding-actions">
          <label className="funding-note">
            Poznámka
            <input
              maxLength={200}
              placeholder="Např. dárce nebo zdroj prostředků"
              {...register("note")}
            />
          </label>
          <button disabled={isSubmitting}>Přidat prostředky</button>
        </div>
        {error && <div className="alert">{error}</div>}
      </form>
      {data?.entries && data.entries.length > 0 && (
        <div className="payment-list compact-history">
          <h3>Získané prostředky</h3>
          {[...data.entries]
            .sort((a, b) => b.entry_date.localeCompare(a.entry_date))
            .map((x) => (
              <article key={x.cofinancing_entry_id}>
                <div>
                  <strong>
                    {new Date(x.entry_date + "T00:00:00").toLocaleDateString(
                      "cs-CZ",
                    )}
                  </strong>
                  {x.note && <small>{x.note}</small>}
                </div>
                <span className="amount">{czk.format(x.amount)}</span>
                <div className="row-actions">
                  <button className="secondary" onClick={() => edit(x)}>
                    Upravit
                  </button>
                  <button className="danger" onClick={() => remove(x)}>
                    Odstranit
                  </button>
                </div>
              </article>
            ))}
        </div>
      )}
    </section>
  );
}
function LumpSumSpending({ id }: { id: string }) {
  const qc = useQueryClient();
  const [error, setError] = useState("");
  const entries = useQuery({
    queryKey: ["lump-entries", id],
    queryFn: () => api<LumpEntry[]>(`/projects/${id}/lump-sum-spending`),
  });
  const {
    register,
    handleSubmit,
    reset,
    formState: { isSubmitting },
  } = useForm<{
    entry_date: string;
    entered_amount: number;
  }>({
    defaultValues: {
      entry_date: new Date().toISOString().slice(0, 10),
      entered_amount: 0,
    },
  });
  async function save(value: { entry_date: string; entered_amount: number }) {
    setError("");
    try {
      await api(`/projects/${id}/lump-sum-spending`, {
        method: "POST",
        body: JSON.stringify({
          ...value,
          entry_mode: "cumulative",
          monitoring_period: "aktuální",
          note: "",
          entered_amount: Number(value.entered_amount),
        }),
      });
      reset({ entry_date: value.entry_date, entered_amount: 0 });
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["lump-entries", id] }),
        qc.invalidateQueries({ queryKey: ["dashboard", id] }),
      ]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Záznam se nepodařilo uložit.");
    }
  }
  async function edit(entry: LumpEntry) {
    const value = prompt("Nová částka v Kč:", String(entry.entered_amount));
    if (value === null) return;
    const amount = Number(value.replace(",", "."));
    if (!Number.isFinite(amount) || amount < 0) {
      setError("Zadejte platnou nezápornou částku.");
      return;
    }
    await api(`/projects/${id}/lump-sum-spending/${entry.lump_sum_entry_id}`, {
      method: "PATCH",
      body: JSON.stringify({ entered_amount: amount }),
    });
    await Promise.all([
      qc.invalidateQueries({ queryKey: ["lump-entries", id] }),
      qc.invalidateQueries({ queryKey: ["dashboard", id] }),
    ]);
  }
  async function remove(entry: LumpEntry) {
    if (!confirm("Opravdu odstranit tento záznam paušální útraty?")) return;
    await api(`/projects/${id}/lump-sum-spending/${entry.lump_sum_entry_id}`, {
      method: "DELETE",
    });
    await Promise.all([
      qc.invalidateQueries({ queryKey: ["lump-entries", id] }),
      qc.invalidateQueries({ queryKey: ["dashboard", id] }),
    ]);
  }
  const activeEntry = entries.data?.reduce<LumpEntry | undefined>(
    (latest, item) =>
      !latest || item.entry_date >= latest.entry_date ? item : latest,
    undefined,
  );
  return (
    <div className="half-row">
      <section className="panel lump-compact">
        <h2>Skutečně utracený paušál</h2>
        <p>
          Zadejte celkovou kumulativní útratu podle účetnictví k vybranému dni.
        </p>
        <form className="inline-form" onSubmit={handleSubmit(save)}>
          <div className="cols">
            <label>
              Kumulativní stav ke dni
              <input
                type="date"
                {...register("entry_date", { required: true })}
              />
            </label>
            <label>
              Celkem utraceno
              <input
                type="number"
                step="0.01"
                min="0"
                {...register("entered_amount", {
                  required: true,
                  valueAsNumber: true,
                })}
              />
            </label>
          </div>
          {error && <div className="alert">{error}</div>}
          <button disabled={isSubmitting}>Uložit stav</button>
        </form>
        {entries.data && entries.data.length > 0 && (
          <div className="payment-list compact-history">
            <h3>Uložené stavy</h3>
            {entries.data.map((x) => {
              const isActive =
                x.lump_sum_entry_id === activeEntry?.lump_sum_entry_id;
              return (
                <article
                  className={
                    isActive ? "current-version" : "historical-version"
                  }
                  key={x.lump_sum_entry_id}
                >
                  <div>
                    <strong>
                      {new Date(x.entry_date + "T00:00:00").toLocaleDateString(
                        "cs-CZ",
                      )}
                    </strong>
                    <small>
                      {isActive ? "Aktuální stav" : "Historická verze"}
                    </small>
                  </div>
                  <span className="amount">{czk.format(x.entered_amount)}</span>
                  <div className="row-actions">
                    <button className="secondary" onClick={() => edit(x)}>
                      Upravit
                    </button>
                    <button className="danger" onClick={() => remove(x)}>
                      Odstranit
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>
      <CofinancingFunding id={id} />
    </div>
  );
}
const SD2_PROJECT_CODE = "CZ.03.02.01/00/25_106/0006125";
const SD2_CODES = ["1.1.1.1", "1.1.1.2", "1.1.1.3", "1.1.2.1", "1.1.3.1"];
const SD2_ITEM_NAMES: Record<string, string> = { "1.1.1.1": "Sociální pracovník", "1.1.1.2": "Casemanager", "1.1.1.3": "Odborný garant", "1.1.2.1": "Odborný garant — DPČ", "1.1.3.1": "Odborný garant — DPP" };
const SD2_MONTHS = [["2026-07-01", "2026-08-01", "2026-09-01", "2026-10-01", "2026-11-01"], ["2026-12-01", "2027-01-01", "2027-02-01", "2027-03-01", "2027-04-01", "2027-05-01"], ["2027-06-01", "2027-07-01", "2027-08-01", "2027-09-01", "2027-10-01", "2027-11-01"], ["2027-12-01", "2028-01-01", "2028-02-01", "2028-03-01", "2028-04-01", "2028-05-01", "2028-06-01"]];
function Sd2MonthlyDialog({ id, period, onClose }: { id: string; period: number; onClose: () => void }) {
  const qc = useQueryClient();
  const { data = [] } = useQuery({ queryKey: ["sd2-monthly", id, period], queryFn: () => api<Sd2Entry[]>(`/projects/${id}/sd2-monthly?period=${period}`) });
  const { data: attachments = [] } = useQuery({ queryKey: ["sd2-attachments", id, period], queryFn: () => api<any[]>(`/projects/${id}/sd2-attachments?period=${period}`) });
  const [changes, setChanges] = useState<Record<string, string | number>>({});
  const [saving, setSaving] = useState(false); const [error, setError] = useState("");
  const months = SD2_MONTHS[period - 1];
  const monthFor = (offset: number) => months[offset];
  const read = (code: string, month: string, field: keyof Sd2Entry) => changes[`${code}|${month}|${field}`] ?? data.find(x => x.budget_item_code === code && x.month === month)?.[field] ?? (field === "payment_date" ? "" : 0);
  const set = (code: string, month: string, field: keyof Sd2Entry, value: string) => setChanges(current => ({ ...current, [`${code}|${month}|${field}`]: value }));
  async function save() {
    setSaving(true); setError("");
    try {
      const entries: Sd2Entry[] = SD2_CODES.flatMap(code => Array.from({ length: months.length }, (_, i) => { const month = monthFor(i); const old = data.find(x => x.budget_item_code === code && x.month === month); return { sd2_entry_id: old?.sd2_entry_id, monitoring_period: period, month, budget_item_code: code, gross_wage: Number(read(code, month, "gross_wage") || 0), employer_contributions: code === "1.1.3.1" ? 0 : Number(read(code, month, "employer_contributions") || 0), other_with_contributions: Number(read(code, month, "other_with_contributions") || 0), other_without_contributions: Number(read(code, month, "other_without_contributions") || 0), payment_date: String(read(code, month, "payment_date") || "") || null }; }));
      await api(`/projects/${id}/sd2-monthly`, { method: "PUT", body: JSON.stringify({ entries }) });
      await qc.invalidateQueries({ queryKey: ["budget-status", id] }); onClose();
    } catch (e) { setError(e instanceof Error ? e.message : "Podklad SD2 se nepodařilo uložit."); } finally { setSaving(false); }
  }
  return <div className="sd2-overlay" role="dialog" aria-modal="true"><section className="sd2-dialog"><div className="sd2-dialog-head"><div><h2>Podklad SD2 — {period}. období</h2><p>Měsíční údaje se zobrazí v příslušném období jako podklad před ŽoP.</p></div><button className="secondary" onClick={onClose}>Zavřít</button></div>{error && <div className="alert">{error}</div>}<div className="sd2-grid-wrap"><table className="sd2-grid"><thead><tr><th>Položka</th>{Array.from({ length: 6 }, (_, i) => <th key={i}>{new Date(`${monthFor(i)}T00:00:00Z`).toLocaleDateString("cs-CZ", { month: "long", year: "numeric" })}</th>)}</tr></thead><tbody>{SD2_CODES.map(code => <tr key={code}><th>{code}</th>{Array.from({ length: 6 }, (_, i) => { const month = monthFor(i); const noContributions = code === "1.1.3.1"; return <td key={month}><label>Hrubá mzda / odměna<input type="number" step="0.01" value={read(code, month, "gross_wage")} onChange={e => set(code, month, "gross_wage", e.target.value)} /></label>{!noContributions && <label>Odvody zaměstnavatele<input type="number" step="0.01" value={read(code, month, "employer_contributions")} onChange={e => set(code, month, "employer_contributions", e.target.value)} /></label>}<label>Jiné výdaje s odvody<input type="number" step="0.01" value={read(code, month, "other_with_contributions")} onChange={e => set(code, month, "other_with_contributions", e.target.value)} /></label><label>Jiné výdaje bez odvodů<input type="number" step="0.01" value={read(code, month, "other_without_contributions")} onChange={e => set(code, month, "other_without_contributions", e.target.value)} /></label><label>Datum úhrady<input type="date" value={read(code, month, "payment_date")} onChange={e => set(code, month, "payment_date", e.target.value)} /></label></td>; })}</tr>)}</tbody></table></div><div className="sd2-save"><button onClick={save} disabled={saving}>{saving ? "Ukládám…" : "Uložit podklad SD2"}</button></div></section></div>;
}
function Sd2MonthlyDialogNew({ id, period, projectCode, projectName, onClose }: { id: string; period: number; projectCode: string; projectName: string; onClose: () => void }) {
  const qc = useQueryClient();
  const isMostyProject = projectName.trim().toLocaleLowerCase("cs-CZ") === "mosty v rodině";
  const { data = [] } = useQuery({ queryKey: ["sd2-monthly", id, period], queryFn: () => api<Sd2Entry[]>(`/projects/${id}/sd2-monthly?period=${period}`) });
  const { data: attachments = [] } = useQuery({ queryKey: ["sd2-attachments", id, period], queryFn: () => api<any[]>(`/projects/${id}/sd2-attachments?period=${period}`) });
  const { data: budgetRows = [] } = useQuery({ queryKey: ["budget-status", id], queryFn: () => api<BudgetRow[]>(`/projects/${id}/budget-status`) });
  const { data: projectSchedule } = useQuery({ queryKey: ["project-schedule", id], queryFn: () => api<ProjectSchedule>(`/projects/${id}/schedule`) });
  const [changes, setChanges] = useState<Record<string, string | number>>({}); const [saving, setSaving] = useState(false); const [error, setError] = useState(""); const [driveToken, setDriveToken] = useState(""); const [uploading, setUploading] = useState(false); const [uploadNotice, setUploadNotice] = useState(""); const [xmlDetails, setXmlDetails] = useState(false); const [defaultSubjectId, setDefaultSubjectId] = useState(""); const [extraMonths, setExtraMonths] = useState<string[]>([]); const [payrollPreview, setPayrollPreview] = useState<PayrollPreview | null>(null); const [payrollMapping, setPayrollMapping] = useState<Record<number, string>>({}); const [payrollProjectHours, setPayrollProjectHours] = useState<Record<number, string>>({}); const [payrollBonusMode, setPayrollBonusMode] = useState<Record<number, "exclude" | "all" | "partial">>({}); const [payrollBonusAmount, setPayrollBonusAmount] = useState<Record<number, string>>({}); const [analyzingPayroll, setAnalyzingPayroll] = useState(false);
  const scheduledPeriod = projectSchedule?.periods.find(item => item.monitoring_period === period);
  const configuredMonths = scheduledPeriod ? monthsInRange(scheduledPeriod.start_month, scheduledPeriod.end_month) : projectCode === SD2_PROJECT_CODE ? (SD2_MONTHS[period - 1] || []) : [];
  const months = Array.from(new Set([...configuredMonths, ...data.map(entry => entry.month), ...extraMonths])).sort();
  const sd2Rows = budgetRows.filter(row => row.is_leaf && row.category === "direct" && /^1\.1\.[123](\.|$)/.test(row.code));
  const sd2Codes = projectCode === SD2_PROJECT_CODE ? SD2_CODES : sd2Rows.map(row => row.code);
  const sd2Names = Object.fromEntries(sd2Rows.map(row => [row.code, row.name]));
  useEffect(() => { const savedSubjectId = data.find(entry => entry.subject_id)?.subject_id; if (savedSubjectId) setDefaultSubjectId(savedSubjectId); }, [data]);
  const numericFields = new Set<keyof Sd2Entry>(["gross_wage", "employer_contributions", "other_with_contributions", "other_without_contributions", "work_time_fund", "project_hours"]);
  const read = (code: string, month: string, field: keyof Sd2Entry) => changes[`${code}|${month}|${field}`] ?? data.find(x => x.budget_item_code === code && x.month === month)?.[field] ?? (numericFields.has(field) ? 0 : "");
  const set = (code: string, month: string, field: keyof Sd2Entry, value: string) => setChanges(current => ({ ...current, [`${code}|${month}|${field}`]: value }));
  const employmentFor = (code: string) => code.startsWith("1.1.2.") ? "DPC" : code.startsWith("1.1.3.") ? "DPP" : "Smlouva";
  const makeEntries = (): Sd2Entry[] => sd2Codes.flatMap(code => months.map(month => {
    const old = data.find(x => x.budget_item_code === code && x.month === month);
    return {
      sd2_entry_id: old?.sd2_entry_id, monitoring_period: period, month, budget_item_code: code,
      gross_wage: Number(read(code, month, "gross_wage") || 0),
      employer_contributions: code === "1.1.3.1" ? 0 : Number(read(code, month, "employer_contributions") || 0),
      other_with_contributions: Number(read(code, month, "other_with_contributions") || 0),
      other_without_contributions: Number(read(code, month, "other_without_contributions") || 0),
      payment_date: String(read(code, month, "payment_date") || "") || null,
      external_id: String(read(code, month, "external_id") || ""),
      subject_id: String(read(code, month, "subject_id") || defaultSubjectId || ""),
      last_name: String(read(code, month, "last_name") || ""), first_name: String(read(code, month, "first_name") || ""),
      employment_type: (String(read(code, month, "employment_type") || employmentFor(code))) as Sd2Entry["employment_type"],
      work_time_fund: Number(read(code, month, "work_time_fund") || 0), project_hours: Number(read(code, month, "project_hours") || 0),
      description: String(read(code, month, "description") || ""),
    };
  }));
  async function save(close = true) { setSaving(true); setError(""); try { await api(`/projects/${id}/sd2-monthly`, { method: "PUT", body: JSON.stringify({ entries: makeEntries() }) }); await Promise.all([qc.invalidateQueries({ queryKey: ["budget-status", id] }), qc.invalidateQueries({ queryKey: ["sd2-monthly", id, period] })]); if (close) onClose(); return true; } catch (e) { setError(e instanceof Error ? e.message : "Podklad SD2 se nepodařilo uložit."); return false; } finally { setSaving(false); } }
  async function exportXml() { const entries = makeEntries(); const hasFinancialData = entries.some(entry => entry.gross_wage || entry.employer_contributions || entry.other_with_contributions || entry.other_without_contributions); if (!hasFinancialData && !window.confirm("V tomto období nejsou vyplněné žádné finanční údaje. Chcete přesto stáhnout prázdné XML SD-2?")) return; setError(""); try { await downloadApi(`/projects/${id}/sd2-xml?period=${period}`, `SD-2_obdobi_${period}.xml`, { method: "POST", body: JSON.stringify({ entries }) }); if (!hasFinancialData) { setUploadNotice("Bylo staženo prázdné XML bez záznamů."); window.setTimeout(() => setUploadNotice(""), 5000); } } catch (e) { setError(e instanceof Error ? e.message : "XML SD-2 se nepodařilo vytvořit."); setXmlDetails(true); } }
  async function clearPeriod() {
    if (!window.confirm(`Opravdu chcete smazat všechny údaje SD-2 v ${period}. období? Soubory uložené na vašem Google Disku zůstanou zachované.`)) return;
    setSaving(true); setError("");
    try {
      await api(`/projects/${id}/sd2-period?period=${period}`, { method: "DELETE" });
      setChanges({}); setExtraMonths([]); setPayrollPreview(null);
      await Promise.all([qc.invalidateQueries({ queryKey: ["sd2-monthly", id, period] }), qc.invalidateQueries({ queryKey: ["sd2-attachments", id, period] }), qc.invalidateQueries({ queryKey: ["budget-status", id] })]);
      setUploadNotice(`Všechny údaje ${period}. období byly smazány.`); window.setTimeout(() => setUploadNotice(""), 5000);
    } catch (e) { setError(e instanceof Error ? e.message : "Údaje období se nepodařilo smazat."); }
    finally { setSaving(false); }
  }
  function applySubjectId() { setChanges(current => { const next = { ...current }; for (const code of sd2Codes) for (const month of months) next[`${code}|${month}|subject_id`] = defaultSubjectId; return next; }); }
  async function analyzePayroll(files: File[]) {
    setAnalyzingPayroll(true); setError(""); setPayrollPreview(null);
    try {
      const form = new FormData(); files.forEach(file => form.append("files", file));
      const preview = await api<PayrollPreview>(`/projects/${id}/payroll-slips/analyze?period=${period}`, { method: "POST", body: form });
      setPayrollPreview(preview);
      setPayrollMapping(Object.fromEntries(preview.rows.map((row, index) => [index, row.budget_item_code || ""])));
      setPayrollProjectHours(Object.fromEntries(preview.rows.map((row, index) => [index, String(row.project_hours ?? row.worked_hours)])));
      setPayrollBonusMode(Object.fromEntries(preview.rows.map((_, index) => [index, "exclude"])));
      setPayrollBonusAmount(Object.fromEntries(preview.rows.map((_, index) => [index, "0"])));
    } catch (e) { setError(e instanceof Error ? e.message : "Výplatní listy se nepodařilo načíst."); }
    finally { setAnalyzingPayroll(false); }
  }
  function selectedProjectBonus(row: PayrollRow, index: number) {
    const mode = payrollBonusMode[index] || "exclude";
    return mode === "all" ? Number(row.project_bonus_available || 0) : mode === "partial" ? Number(payrollBonusAmount[index] || 0) : 0;
  }
  function payrollDescription(row: PayrollRow, index: number, projectHours: number) {
    const number = (value: number, digits = 2) => new Intl.NumberFormat("cs-CZ", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value);
    const hours = (value: number) => number(value, Number.isInteger(value) ? 0 : 2);
    if (row.employment_type === "Smlouva") {
      const vacationHours = Number(row.vacation_hours || 0);
      const vacationDays = Number(row.vacation_days || 0);
      const projectVacation = row.project_vacation_hours != null ? Number(row.project_vacation_hours) : Number(row.work_time_fund) ? vacationHours * projectHours / Number(row.work_time_fund) : 0;
      const parts = [`Pracovní smlouva; celková výše úvazku u zaměstnavatele: ${number(Number(row.total_fte || 0))}; dovolená: ${hours(vacationDays)} dní, ${hours(vacationHours)} hodin celkem, z toho ${hours(projectVacation)} hodin pro projekt.`];
      const availableBonus = Number(row.project_bonus_available || 0);
      if (availableBonus > 0) {
        const selectedBonus = selectedProjectBonus(row, index);
        if (selectedBonus <= 0) parts.push(`Mimořádná odměna ${czk.format(availableBonus)} – mimo projekt.`);
        else if (selectedBonus >= availableBonus) parts.push(`Mimořádná odměna ${czk.format(availableBonus)} – celá zahrnuta do projektu.`);
        else parts.push(`Mimořádná odměna ${czk.format(availableBonus)}; do projektu zahrnuto ${czk.format(selectedBonus)}.`);
      }
      return parts.join(" ");
    }
    const relation = row.employment_type === "DPC" ? "Dohoda o pracovní činnosti" : row.employment_type?.startsWith("DPP") ? "Dohoda o provedení práce" : "Pracovněprávní vztah";
    return `${relation}; fond vztahu: ${hours(Number(row.work_time_fund || 0))} hodin; počet hodin na projektu: ${hours(projectHours)}.`;
  }
  function applyPayroll() {
    if (!payrollPreview) return;
    if (payrollPreview.rows.some((_, index) => !payrollMapping[index])) { setError("U každé mzdové složky vyberte rozpočtovou položku nebo Nezahrnovat do projektu."); return; }
    const included = payrollPreview.rows.map((row, index) => ({ row, index, code: payrollMapping[index] })).filter(item => item.code !== "__ignore__");
    if (!included.length) { setError("Nebyla vybrána žádná mzdová složka pro projekt."); return; }
    if (configuredMonths.length && included.some(item => !configuredMonths.includes(item.row.month))) { setError(`Výplatní listy nepatří do ${period}. monitorovacího období.`); return; }
    const invalidBonus = included.some(({ row, index }) => {
      if (payrollBonusMode[index] !== "partial") return false;
      const amount = Number(payrollBonusAmount[index]);
      return !Number.isFinite(amount) || amount < 0 || amount > Number(row.project_bonus_available || 0);
    });
    if (invalidBonus) { setError("Část projektové prémie musí být mezi 0 Kč a nalezenou částkou prémie."); return; }
    const peopleByKey = new Map<string, Set<string>>();
    included.forEach(({ row, code }) => { const key = `${code}|${row.month}`; const people = peopleByKey.get(key) || new Set<string>(); people.add(`${row.first_name}|${row.last_name}`); peopleByKey.set(key, people); });
    if ([...peopleByKey.values()].some(people => people.size > 1)) { setError("Dva různí pracovníci jsou přiřazeni ke stejné položce a měsíci. Pro XML SD-2 potřebují samostatné rozpočtové položky."); return; }
    setExtraMonths(current => Array.from(new Set([...current, ...included.map(item => item.row.month)])));
    setChanges(current => {
      const next = { ...current };
      const grouped = new Map<string, { row: PayrollRow; gross: number; contributions: number; correction: number; fund: number; projectHours: number; description: string }>();
      included.forEach(({ row, index, code }) => {
        const groupKey = `${code}|${row.month}`; const existing = grouped.get(groupKey);
        const projectHours = Number(payrollProjectHours[index] ?? row.project_hours ?? row.worked_hours) || 0;
        const projectBonus = selectedProjectBonus(row, index);
        const contributions = Number(row.employer_contributions) + projectBonus * Number(row.employer_contribution_rate ?? 0.338);
        const correction = Number(row.other_with_contributions || 0) + projectBonus;
        const description = payrollDescription(row, index, projectHours);
        if (existing) { existing.gross += Number(row.gross_wage); existing.contributions += contributions; existing.correction += correction; existing.fund = Math.max(existing.fund, Number(row.work_time_fund)); existing.projectHours = Math.max(existing.projectHours, projectHours); if (!existing.description.includes(description)) existing.description += ` ${description}`; }
        else grouped.set(groupKey, { row, gross: Number(row.gross_wage), contributions, correction, fund: Number(row.work_time_fund), projectHours, description });
      });
      grouped.forEach(({ row, gross, contributions, correction, fund, projectHours, description }, groupKey) => {
        const [code, month] = groupKey.split("|"); const key = `${code}|${month}|`;
        Object.assign(next, {
          [`${key}gross_wage`]: Math.round(gross * 100) / 100, [`${key}employer_contributions`]: Math.round(contributions * 100) / 100,
          [`${key}other_with_contributions`]: Math.round(correction * 100) / 100,
          [`${key}work_time_fund`]: fund, [`${key}project_hours`]: projectHours,
          [`${key}first_name`]: row.first_name, [`${key}last_name`]: row.last_name,
          [`${key}employment_type`]: row.employment_type || employmentFor(code),
          [`${key}subject_id`]: row.subject_id || defaultSubjectId,
          [`${key}description`]: description,
        });
      });
      return next;
    });
    setPayrollPreview(null); setUploadNotice("Údaje z výplatních listů byly načteny. Doplňte datum úhrady a zkontrolujte projektové hodiny."); window.setTimeout(() => setUploadNotice(""), 9000);
  }
  async function connectDrive() {
    setError("");
    try {
      setDriveToken(await requestDriveAccessToken());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Nepodařilo se připojit Google Drive.");
    }
  }
  async function upload(file: File) {
    setError("");
    setUploading(true);
    try {
      if (!driveToken) throw new Error("Nejprve připojte Google Drive.");
      const driveFileId = await uploadSd2ArchiveToUserDrive(file, id, period, driveToken);
      await api(`/projects/${id}/sd2-attachments/record?period=${period}`, {
        method: "POST",
        body: JSON.stringify({ file_name: file.name, drive_file_id: driveFileId }),
      });
      await qc.invalidateQueries({ queryKey: ["sd2-attachments", id, period] });
      setUploadNotice("Archiv byl uložen na Google Drive.");
      window.setTimeout(() => setUploadNotice(""), 5000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Archiv se nepodařilo uložit.");
    } finally {
      setUploading(false);
    }
  }
  return <div className="sd2-overlay" role="dialog" aria-modal="true"><section className="sd2-dialog">
    <div className="sd2-dialog-head">
      <div><h2>Podklad SD2 — {period}. období</h2><p>Měsíční údaje se zobrazí v příslušném období jako podklad před ŽoP.</p></div>
      <div className="sd2-attachments"><label className="upload-button">{analyzingPayroll ? "Načítám mzdové podklady…" : "Načíst výplatní listy a pásky PDF"}<input type="file" accept=".pdf,application/pdf" multiple disabled={analyzingPayroll} onChange={e => e.target.files?.length && analyzePayroll(Array.from(e.target.files))} /></label>{!isMostyProject && (driveToken ? <label className="upload-button">{uploading ? "Ukládám archiv…" : "Vybrat archiv ZIP / RAR"}<input type="file" accept=".zip,.rar" disabled={uploading} onChange={e => e.target.files?.[0] && upload(e.target.files[0])} /></label> : <button className="upload-button" onClick={connectDrive}>Připojit Google Drive</button>)}{uploadNotice && <small className="sd2-upload-notice">{uploadNotice}</small>}</div>
      <button className="secondary" onClick={onClose}>Zavřít</button>
    </div>
    {error && <div className="alert sd2-error">{error}</div>}
    {payrollPreview && <section className="payroll-preview">
      <div className="payroll-preview-head"><div><h3>Kontrola načtených výplatních listů</h3><p>U nalezené prémie rozhodněte, zda patří do projektu. Výchozí volba ji do projektu nezahrne.</p></div><button type="button" className="secondary" onClick={() => setPayrollPreview(null)}>Zrušit</button></div>
      <div className="table-wrap"><table><thead><tr><th>Pracovník</th><th>Pracovní vztah</th><th>Mzdová složka</th><th>Měsíc</th><th>Částka</th><th>Prémie do projektu</th><th>Pojistné</th><th>Fond</th><th>Hodiny projektu</th><th>Rozpočtová položka</th></tr></thead><tbody>
        {payrollPreview.rows.map((row, index) => <tr key={row.source_key || `${row.page_number}-${index}`}>
          <td><b>{row.full_name}</b><small>{row.position_name}</small></td><td>{row.contract_name || row.category}<small>{row.employment_type}</small></td><td>{row.component_name || "Hrubá mzda"}<small>{row.component_description}</small></td><td>{new Date(`${row.month}T00:00:00Z`).toLocaleDateString("cs-CZ", { month: "long", year: "numeric" })}</td><td>{czk.format(row.component_amount ?? row.gross_wage)}</td>
          <td>{Number(row.project_bonus_available || 0) > 0 ? <div className="payroll-bonus"><small>Nalezena {row.project_bonus_label || "prémie"}: <b>{czk.format(Number(row.project_bonus_available))}</b></small><select value={payrollBonusMode[index] || "exclude"} onChange={e => setPayrollBonusMode(current => ({ ...current, [index]: e.target.value as "exclude" | "all" | "partial" }))}><option value="exclude">Nepatří do projektu</option><option value="all">Celá patří do projektu</option><option value="partial">Patří jen část</option></select>{payrollBonusMode[index] === "partial" && <input aria-label="Část prémie patřící do projektu" type="number" min="0" max={row.project_bonus_available} step="0.01" value={payrollBonusAmount[index] || "0"} onChange={e => setPayrollBonusAmount(current => ({ ...current, [index]: e.target.value }))} />}</div> : <span className="muted">—</span>}</td>
          <td>{czk.format(Number(row.employer_contributions) + selectedProjectBonus(row, index) * Number(row.employer_contribution_rate ?? 0.338))}</td><td>{row.work_time_fund}</td><td><input type="number" min="0" step="0.01" value={payrollProjectHours[index] ?? row.project_hours ?? row.worked_hours} onChange={e => setPayrollProjectHours(current => ({ ...current, [index]: e.target.value }))} /></td><td><select className={!payrollMapping[index] ? "unmatched" : ""} value={payrollMapping[index] || ""} onChange={e => setPayrollMapping(current => ({ ...current, [index]: e.target.value }))}><option value="">Vyberte položku…</option><option value="__ignore__">Nezahrnovat do projektu</option>{payrollPreview.budget_items.map(item => <option key={item.code} value={item.code}>{item.code} — {item.name}</option>)}</select></td>
        </tr>)}
      </tbody></table></div><div className="payroll-preview-actions"><button type="button" onClick={applyPayroll}>Převzít do SD-2</button></div>
    </section>}
    {!months.length && !payrollPreview && <div className="sd2-empty"><b>V tomto období zatím nejsou měsíční údaje.</b><span>Nahrajte PDF s výplatními listy; měsíc se načte automaticky.</span></div>}
    <div className="sd2-grid-wrap"><table className="sd2-grid"><thead><tr><th>Položka</th>{months.map(month => <th key={month}>{new Date(`${month}T00:00:00Z`).toLocaleDateString("cs-CZ", { month: "long", year: "numeric" })}</th>)}</tr></thead><tbody>{sd2Codes.map(code => <tr key={code}><th><b>{code}</b><small>{SD2_ITEM_NAMES[code] || sd2Names[code]}</small></th>{months.map(month => { const noContributions = code === "1.1.3.1"; return <td key={month}><label>Hrubá mzda / odměna<input type="number" step="0.01" value={read(code, month, "gross_wage")} onChange={e => set(code, month, "gross_wage", e.target.value)} /></label>{!noContributions && <label>Odvody zaměstnavatele<input type="number" step="0.01" value={read(code, month, "employer_contributions")} onChange={e => set(code, month, "employer_contributions", e.target.value)} /></label>}<label>Jiné výdaje s odvody<input type="number" step="0.01" value={read(code, month, "other_with_contributions")} onChange={e => set(code, month, "other_with_contributions", e.target.value)} /></label><label>Jiné výdaje bez odvodů<input type="number" step="0.01" value={read(code, month, "other_without_contributions")} onChange={e => set(code, month, "other_without_contributions", e.target.value)} /></label><label>Datum úhrady<input type="date" value={read(code, month, "payment_date")} onChange={e => set(code, month, "payment_date", e.target.value)} /></label></td>; })}</tr>)}</tbody></table></div>
    <button className="sd2-details-toggle secondary" type="button" onClick={() => setXmlDetails(value => !value)}>{xmlDetails ? "Skrýt údaje pro XML" : "Doplnit údaje pro XML"}</button>
    {xmlDetails && <section className="sd2-xml-panel">
      <div className="sd2-xml-heading"><div><h3>Údaje pro import XML do IS KP21+</h3><p>Vyplňte údaje u řádků, ve kterých vykazujete výdaj. Technické ID vytvoří aplikace automaticky.</p></div><label>Výchozí IČ subjektu<div className="sd2-subject-apply"><input inputMode="numeric" maxLength={10} value={defaultSubjectId} onChange={e => setDefaultSubjectId(e.target.value.replace(/\D/g, ""))} /><button type="button" className="secondary" onClick={applySubjectId}>Použít všude</button></div></label></div>
      <div className="sd2-xml-table-wrap"><table className="sd2-xml-table"><thead><tr><th>Měsíc</th><th>Položka</th><th>IČ</th><th>Jméno</th><th>Příjmení</th><th>Pracovní vztah</th><th>Fond hodin</th><th>Hodiny projektu</th><th>Datum úhrady</th><th>Popis</th></tr></thead><tbody>{sd2Codes.flatMap(code => months.map(month => <tr key={`${code}-${month}`}><td>{new Date(`${month}T00:00:00Z`).toLocaleDateString("cs-CZ", { month: "2-digit", year: "numeric" })}</td><td><b>{code}</b></td><td><input inputMode="numeric" maxLength={10} value={read(code, month, "subject_id")} onChange={e => set(code, month, "subject_id", e.target.value.replace(/\D/g, ""))} /></td><td><input value={read(code, month, "first_name")} onChange={e => set(code, month, "first_name", e.target.value)} /></td><td><input value={read(code, month, "last_name")} onChange={e => set(code, month, "last_name", e.target.value)} /></td><td><select value={read(code, month, "employment_type") || employmentFor(code)} onChange={e => set(code, month, "employment_type", e.target.value)}><option value="Smlouva">Pracovní smlouva</option><option value="DPC">DPČ</option><option value="DPP">DPP od roku 2025</option><option value="DPPDo">DPP do 10 tis. (do 2024)</option><option value="DPPNad">DPP nad 10 tis. (do 2024)</option></select></td><td><input type="number" min="0" step="0.01" value={read(code, month, "work_time_fund")} onChange={e => set(code, month, "work_time_fund", e.target.value)} /></td><td><input type="number" min="0" step="0.01" value={read(code, month, "project_hours")} onChange={e => set(code, month, "project_hours", e.target.value)} /></td><td><input type="date" value={read(code, month, "payment_date")} onChange={e => set(code, month, "payment_date", e.target.value)} /></td><td><input maxLength={2000} value={read(code, month, "description")} onChange={e => set(code, month, "description", e.target.value)} /></td></tr>))}</tbody></table></div>
    </section>}
    {error && <div className="alert sd2-error sd2-footer-error">{error}</div>}
    <div className="sd2-save">{projectName.trim().toLocaleLowerCase("cs-CZ") === "mosty v rodině" && <button className="danger sd2-clear-period" type="button" onClick={clearPeriod} disabled={saving}>Smazat vše</button>}<button className="secondary" type="button" onClick={() => setXmlDetails(true)}>Kontrola údajů XML</button><button type="button" onClick={exportXml} disabled={saving}>Stáhnout XML SD-2</button><button type="button" onClick={() => save()} disabled={saving}>{saving ? "Ukládám…" : "Uložit podklad SD2"}</button></div>
  </section></div>;
}
function BudgetWorkerSettings({ id, versionId, periodCount, onClose }: { id: string; versionId?: string; periodCount: number; onClose: () => void }) {
  const qc = useQueryClient();
  const budgetQuery = useQuery({ queryKey: ["budget-status", id, versionId], queryFn: () => api<BudgetRow[]>(`/projects/${id}/budget-status${versionId ? `?version_id=${encodeURIComponent(versionId)}` : ""}`) });
  const savedQuery = useQuery({ queryKey: ["worker-assignments", id], queryFn: () => api<WorkerAssignment[]>(`/projects/${id}/worker-assignments`) });
  const scheduleQuery = useQuery({ queryKey: ["project-schedule", id], queryFn: () => api<ProjectSchedule>(`/projects/${id}/schedule`) });
  const rows = budgetQuery.data || [];
  const saved = savedQuery.data || [];
  const [names, setNames] = useState<Record<string, string[]>>({}); const [saving, setSaving] = useState(false); const [error, setError] = useState("");
  const [projectStart, setProjectStart] = useState(""); const [projectEnd, setProjectEnd] = useState(""); const [periodMonths, setPeriodMonths] = useState<number[]>(Array.from({ length: periodCount }, () => 0));
  useEffect(() => setNames(Object.fromEntries(saved.map(item => [item.budget_item_code, item.employee_names.split(/[,;\n]+/).map(name => name.trim()).filter(Boolean)]))), [saved]);
  useEffect(() => { const schedule = scheduleQuery.data; if (!schedule) return; setProjectStart(schedule.project_start_date || ""); setProjectEnd(schedule.project_end_date || ""); setPeriodMonths(Array.from({ length: periodCount }, (_, index) => { const range = schedule.periods.find(item => item.monitoring_period === index + 1); return range ? monthCountInclusive(range.start_month, range.end_month) : 0; })); }, [scheduleQuery.data, periodCount]);
  const positions = rows.filter(row => row.is_leaf && row.category === "direct");
  function updateName(code: string, index: number, value: string) { setNames(current => { const list = [...(current[code]?.length ? current[code] : [""])]; list[index] = value; return { ...current, [code]: list }; }); }
  function addName(code: string) { setNames(current => ({ ...current, [code]: [...(current[code] || []), ""] })); }
  function removeName(code: string, index: number) { setNames(current => { const list = (current[code] || []).filter((_, itemIndex) => itemIndex !== index); return { ...current, [code]: list.length ? list : [""] }; }); }
  function distributeMonths() { const total = monthCountInclusive(projectStart, projectEnd); if (total < periodCount) { setError("Projekt musí mít alespoň jeden měsíc pro každé období."); return; } const base = Math.floor(total / periodCount); const remainder = total % periodCount; setPeriodMonths(Array.from({ length: periodCount }, (_, index) => base + (index < remainder ? 1 : 0))); setError(""); }
  function periodRange(index: number) { const offset = periodMonths.slice(0, index).reduce((sum, value) => sum + Number(value || 0), 0); const count = Number(periodMonths[index] || 0); return count > 0 && projectStart ? { start: addMonths(projectStart, offset), end: addMonths(projectStart, offset + count - 1) } : null; }
  async function save() { setSaving(true); setError(""); try { const requests: Promise<unknown>[] = [api(`/projects/${id}/worker-assignments`, { method: "PUT", body: JSON.stringify({ assignments: positions.map(row => ({ budget_item_code: row.code, employee_names: (names[row.code] || []).map(name => name.trim()).filter(Boolean).join(", ") })) }) })]; if (projectStart || projectEnd || periodMonths.some(Boolean)) { if (!projectStart || !projectEnd) throw new Error("Vyplňte začátek i konec projektu."); const total = monthCountInclusive(projectStart, projectEnd); if (periodMonths.some(value => !Number.isInteger(Number(value)) || Number(value) < 1) || periodMonths.reduce((sum, value) => sum + Number(value), 0) !== total) throw new Error(`Rozdělte mezi období všech ${total} měsíců projektu.`); requests.push(api(`/projects/${id}/schedule`, { method: "PUT", body: JSON.stringify({ project_start_date: projectStart, project_end_date: projectEnd, periods: periodMonths.map((_, index) => { const range = periodRange(index)!; return { monitoring_period: index + 1, start_month: range.start, end_month: range.end }; }) }) })); } await Promise.all(requests); await Promise.all([qc.invalidateQueries({ queryKey: ["worker-assignments", id] }), qc.invalidateQueries({ queryKey: ["project-schedule", id] })]); onClose(); } catch (e) { setError(e instanceof Error ? e.message : "Nastavení se nepodařilo uložit."); } finally { setSaving(false); } }
  const loading = budgetQuery.isLoading || savedQuery.isLoading || scheduleQuery.isLoading;
  const loadError = budgetQuery.error || savedQuery.error || scheduleQuery.error;
  return <div className="sd2-overlay" role="dialog" aria-modal="true"><section className="worker-settings">
    <div className="worker-settings-head"><div><h2>Nastavení projektu</h2><p>Nastavte harmonogram monitorovacích období a pracovníky přiřazené k rozpočtovým položkám.</p></div><button className="secondary" onClick={onClose}>Zavřít</button></div>
    {error && <div className="alert">{error}</div>}
    {loadError && <div className="alert">Nepodařilo se načíst nastavení projektu.</div>}
    <section className="project-schedule-settings">
      <div className="settings-section-head"><div><h3>Harmonogram projektu</h3><p>Rozdělte všechny kalendářní měsíce projektu mezi monitorovací období.</p></div><button type="button" className="secondary" onClick={distributeMonths}>Rozdělit rovnoměrně</button></div>
      <div className="project-date-fields"><label>Začátek projektu<input type="date" value={projectStart} onChange={e => setProjectStart(e.target.value)} /></label><label>Konec projektu<input type="date" value={projectEnd} onChange={e => setProjectEnd(e.target.value)} /></label><div className="schedule-total"><small>Celkem</small><b>{monthCountInclusive(projectStart, projectEnd) || "—"} měsíců</b></div></div>
      <div className="period-month-settings">{periodMonths.map((count, index) => { const range = periodRange(index); return <label key={index}><b>{index + 1}. období</b><span><input type="number" min="1" step="1" value={count || ""} onChange={e => setPeriodMonths(current => current.map((value, itemIndex) => itemIndex === index ? Number(e.target.value) : value))} /> měsíců</span><small>{range ? `${monthLabel(range.start)} – ${monthLabel(range.end)}` : "Zadejte počet měsíců"}</small></label>; })}</div>
    </section>
    <section className="worker-assignment-settings"><h3>Pracovníci v rozpočtových položkách</h3><p>Ke každé mzdové položce doplňte jednoho nebo více zaměstnanců. Nastavení se použije při automatickém načítání výplatních pásek.</p>
      {loading ? <p>Načítám nastavení…</p> : positions.length === 0 ? <div className="info">V této verzi rozpočtu nebyly nalezeny žádné přímé mzdové položky.</div> : <div className="table-wrap"><table><thead><tr><th>Kód</th><th>Pozice / položka rozpočtu</th><th>Zaměstnanci</th></tr></thead><tbody>{positions.map(row => { const employees = names[row.code]?.length ? names[row.code] : [""]; return <tr key={row.code}><td><b>{row.code}</b></td><td>{row.name}</td><td><div className="worker-name-list">{employees.map((employee, index) => <div className="worker-name-row" key={`${row.code}-${index}`}><input value={employee} placeholder="Např. Jana Nováková" onChange={e => updateName(row.code, index, e.target.value)} /><button type="button" className="secondary worker-remove" onClick={() => removeName(row.code, index)} aria-label={`Odstranit zaměstnance u položky ${row.code}`}>×</button></div>)}<button type="button" className="secondary worker-add" onClick={() => addName(row.code)}>+ Přidat zaměstnance</button></div></td></tr>; })}</tbody></table></div>}
    </section>
    <div className="sd2-save"><button onClick={save} disabled={saving || loading || Boolean(loadError)}>{saving ? "Ukládám…" : "Uložit nastavení"}</button></div>
  </section></div>;
}
function BudgetOverview({
  id,
  periodCount,
  activeVersionId,
  projectCode,
  projectName,
}: {
  id: string;
  periodCount: number;
  activeVersionId?: string | null;
  projectCode?: string;
  projectName?: string;
}) {
  const versions = useQuery({
    queryKey: ["budget-versions", id],
    queryFn: () => api<BudgetVersion[]>(`/projects/${id}/budgets`),
  });
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => api<CurrentUser>("/me"),
  });
  const qc = useQueryClient();
  const [deleteError, setDeleteError] = useState(""); const [exportingBudget, setExportingBudget] = useState(false);
  const [sd2Period, setSd2Period] = useState<number | null>(null); const [workerSettingsOpen, setWorkerSettingsOpen] = useState(false);
  const [selectedVersion, setSelectedVersion] = useState(activeVersionId ?? "");
  useEffect(() => {
    if (activeVersionId) setSelectedVersion(activeVersionId);
    else if (!selectedVersion && versions.data?.length)
      setSelectedVersion(versions.data[versions.data.length - 1].version_id);
  }, [activeVersionId, selectedVersion, versions.data]);
  const effectiveActive =
    activeVersionId ??
    versions.data?.[versions.data.length - 1]?.version_id ??
    null;
  const { data = [] } = useQuery({
    queryKey: ["budget-status", id, selectedVersion],
    queryFn: () =>
      api<BudgetRow[]>(
        `/projects/${id}/budget-status${selectedVersion ? `?version_id=${encodeURIComponent(selectedVersion)}` : ""}`,
      ),
  });
  const periods = Array.from(
    { length: Math.min(6, Math.max(1, periodCount || 1)) },
    (_, i) => String(i + 1),
  );
  const selectedIndex =
    versions.data?.findIndex((v) => v.version_id === selectedVersion) ?? -1;
  const historical = Boolean(
    selectedVersion && effectiveActive && selectedVersion !== effectiveActive,
  );
  async function removeSelectedVersion() {
    if (!selectedVersion || !window.confirm("Opravdu chcete tuto verzi rozpočtu trvale smazat?")) return;
    setDeleteError("");
    try {
      await api(`/projects/${id}/budgets/${selectedVersion}`, { method: "DELETE" });
      setSelectedVersion("");
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["project", id] }),
        qc.invalidateQueries({ queryKey: ["dashboard", id] }),
        qc.invalidateQueries({ queryKey: ["budget-versions", id] }),
        qc.invalidateQueries({ queryKey: ["budget-status", id] }),
      ]);
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : "Rozpočet se nepodařilo smazat.");
    }
  }
  async function downloadBudgetXlsx() {
    setDeleteError(""); setExportingBudget(true);
    try {
      await downloadApi(`/projects/${id}/budget-status.xlsx${selectedVersion ? `?version_id=${encodeURIComponent(selectedVersion)}` : ""}`, "Cerpani_rozpoctu_mesicne.xlsx");
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : "Přehled čerpání se nepodařilo stáhnout.");
    } finally { setExportingBudget(false); }
  }
  return (
    <section className="panel wide budget-overview">
      <div className="budget-overview-head">
        <div className="budget-heading">
          <h2>Čerpání rozpočtu po položkách</h2>
          {historical && <p>Historický pohled na verzi {selectedIndex + 1}. Čerpání je promítnuto proti tehdejším částkám.</p>}
        </div>
        <div className="budget-tools">
          <label className="version-select">
            Verze rozpočtu
            <select value={selectedVersion} onChange={(e) => setSelectedVersion(e.target.value)}>
              {versions.data?.map((version, index) => <option key={version.version_id} value={version.version_id}>{version.version_id === effectiveActive ? `Aktuální verze ${index + 1}` : index === 0 ? "Původní rozpočet" : `Verze ${index + 1}`}</option>)}
            </select>
          </label>
          <button className="secondary" type="button" onClick={downloadBudgetXlsx} disabled={exportingBudget}>{exportingBudget ? "Připravuji XLSX…" : "Stáhnout čerpání XLSX"}</button>
          {me.data?.role === "admin" && (
            <>
              <ImportBudget id={id} compact />
              <BudgetChange id={id} compact />
              <button className="secondary budget-settings-button" type="button" title="Nastavení pracovníků v rozpočtových položkách" onClick={() => setWorkerSettingsOpen(true)}>⚙ Nastavení</button>
              {selectedVersion && <button className="danger budget-delete-version" type="button" onClick={removeSelectedVersion}>Smazat verzi</button>}
            </>
          )}
        </div>
      </div>
      {deleteError && <p className="error">{deleteError}</p>}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Kód a položka</th>
              <th>Rozpočet</th>
              {periods.map((p) => (
                <th
                  className="period-col sd2-period-button"
                  key={p}
                  onClick={() => setSd2Period(Number(p))}
                  role="button"
                  title={`Otevřít podklad SD2 pro ${p}. období`}
                >
                  <span>{p}. období</span>
                  <small>Vyplnit SD2</small>
                </th>
              ))}
              <th>Kumulativně</th>
              <th>Zůstatek</th>
              <th>Čerpání</th>
            </tr>
          </thead>
          <tbody>
            {data.map((row) => (
              <tr
                key={row.code}
                className={
                  row.remaining < 0
                    ? "overdrawn"
                    : row.remaining < row.total_amount * 0.1
                      ? "warning-row"
                      : ""
                }
              >
                <td style={{ paddingLeft: 12 + row.level * 16 }}>
                  <strong>{row.code}</strong> {row.name}
                  {row.has_budget_change && (
                    <span className="budget-change-tip">
                      <InfoTip text={row.change_note} />
                    </span>
                  )}
                </td>
                <td>{czk.format(row.total_amount)}</td>
                {periods.map((p) => (
                  <td
                    className={`period-col ${p in (row.periods ?? {}) ? "" : "inactive-period"}`}
                    key={p}
                  >
                    {p in (row.periods ?? {})
                      ? czk.format(row.periods[p])
                      : "—"}
                  </td>
                ))}
                <td>{czk.format(row.cumulative_spent)}</td>
                <td>{czk.format(row.remaining)}</td>
                <td>{pct.format(row.spent_percent)} %</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {sd2Period && <Sd2MonthlyDialogNew id={id} period={sd2Period} projectCode={projectCode || ""} projectName={projectName || ""} onClose={() => setSd2Period(null)} />}
      {workerSettingsOpen && <BudgetWorkerSettings id={id} versionId={selectedVersion} periodCount={periodCount} onClose={() => setWorkerSettingsOpen(false)} />}
    </section>
  );
}
function BudgetChange({
  id,
  compact = false,
}: {
  id: string;
  compact?: boolean;
}) {
  const [preview, setPreview] = useState<any>();
  const [proposal, setProposal] = useState<any>();
  const [error, setError] = useState("");
  const qc = useQueryClient();
  async function analyze(file: File) {
    setError("");
    const fd = new FormData();
    fd.append("file", file);
    try {
      setPreview(
        await api(`/projects/${id}/budget-change/analyze`, {
          method: "POST",
          body: fd,
        }),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Analýza selhala.");
    }
  }
  async function activate() {
    await api(`/projects/${id}/budget-change/import`, {
      method: "POST",
      body: JSON.stringify({ token: preview.token }),
    });
    setPreview(null);
    qc.invalidateQueries({ queryKey: ["budget-status", id] });
    qc.invalidateQueries({ queryKey: ["dashboard", id] });
  }
  async function generate() {
    setProposal(
      await api(`/projects/${id}/change-proposals/generate`, {
        method: "POST",
        body: JSON.stringify({ reserve_rate: 0 }),
      }),
    );
  }
  const content = (
    <>
      <div className="change-actions">
        <label className="upload-button">
          {compact ? "Změnový rozpočet" : "Nahrát změnový rozpočet XLSX"}
          <input
            type="file"
            accept=".xlsx"
            onChange={(e) => e.target.files?.[0] && analyze(e.target.files[0])}
          />
        </label>
        <span className="action-with-tip">
          <button className="secondary" onClick={generate}>
            Návrh přesunů
          </button>
          <InfoTip text="Navrhne změnu rozpočtu v případě přečerpání položky/položek." />
        </span>
      </div>
      {error && <div className="alert">{error}</div>}
      {preview && (
        <div className="preview budget-tool-preview">
          <h3>Porovnání verzí</h3>
          <p>
            Původní celkem: <strong>{czk.format(preview.current_total)}</strong>{" "}
            · Nově: <strong>{czk.format(preview.total_amount)}</strong>
          </p>
          {preview.errors?.map((x: string) => (
            <div className="alert" key={x}>
              {x}
            </div>
          ))}
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Kód</th>
                  <th>Položka</th>
                  <th>Původně</th>
                  <th>Nově</th>
                  <th>Rozdíl</th>
                  <th>Stav</th>
                </tr>
              </thead>
              <tbody>
                {preview.changes
                  .filter((x: any) => x.status !== "beze změny")
                  .map((x: any) => (
                    <tr key={x.code}>
                      <td>{x.code}</td>
                      <td>{x.name}</td>
                      <td>{czk.format(x.old_amount)}</td>
                      <td>{czk.format(x.new_amount)}</td>
                      <td>{czk.format(x.difference)}</td>
                      <td>{x.status}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
          <button disabled={preview.errors?.length > 0} onClick={activate}>
            Aktivovat novou verzi
          </button>
        </div>
      )}
      {proposal && (
        <div className="preview budget-tool-preview">
          <h3>Návrh přesunů</h3>
          {proposal.deficits.length === 0 ? (
            <p className="info">Žádná koncová položka není přečerpaná.</p>
          ) : proposal.transfers.length === 0 ? (
            <div className="alert">
              Deficit nelze pokrýt z dostupných položek.
            </div>
          ) : (
            <>
              {!proposal.balanced && (
                <div className="alert">
                  Reálně nevyčerpané položky nemají dostatečnou rezervu k pokrytí celého přečerpání.
                </div>
              )}
              {proposal.feasibility_errors?.map((message: string) => (
                <div className="alert" key={message}>{message}</div>
              ))}
              {Number(proposal.transfer_reserve) > 0 && (
                <p className="info">
                  Kvůli zachování počtu jednotek a ceny maximálně na dvě desetinná místa návrh převádí o {czk.format(Number(proposal.transfer_reserve))} více než samotné přečerpání.
                </p>
              )}
              <div className="table-wrap"><table>
                <thead>
                  <tr>
                    <th>Zdroj</th>
                    <th>Cíl</th>
                    <th>Částka</th>
                  </tr>
                </thead>
                <tbody>
                  {proposal.transfers.map((x: any, i: number) => (
                    <tr key={i}>
                      <td>{x.source_code}</td>
                      <td>{x.target_code}</td>
                      <td>{czk.format(x.amount)}</td>
                    </tr>
                  ))}
                </tbody>
              </table></div>
              {proposal.balanced && (
                <button className="secondary" type="button" onClick={() =>
                  downloadApi(`/projects/${id}/change-proposals/${proposal.proposal_id}/download`, "Navrh_zmeny_rozpoctu.xlsx")
                    .catch((e) => setError(e instanceof Error ? e.message : "Soubor se nepodařilo stáhnout."))}>
                  {proposal.feasible ? "Stáhnout proveditelný rozpočet XLSX" : "Stáhnout kontrolní návrh XLSX"}
                </button>
              )}
            </>
          )}
        </div>
      )}
    </>
  );
  return compact ? (
    <div className="budget-tool">{content}</div>
  ) : (
    <section className="panel">
      <h2>Změnový rozpočet a návrh přesunů</h2>
      {content}
    </section>
  );
}
function FinalSettlement({ id }: { id: string }) {
  const { data } = useQuery({
    queryKey: ["final-settlement", id],
    queryFn: () => api<any>(`/projects/${id}/final-settlement`),
  });
  if (!data) return null;
  if (!data.has_final_payment)
    return (
      <section className="settlement-strip">
        <strong>Orientační závěrečné vypořádání</strong>
        <span>
          Výpočet vratky nebo doplatku se zobrazí po nahrání závěrečné žádosti o
          platbu.
        </span>
      </section>
    );
  const result = Number(data.settlement);
  return (
    <section className={`settlement ${result < 0 ? "refund" : "supplement"}`}>
      <div>
        <small>ZÁVĚREČNÉ VYPOŘÁDÁNÍ</small>
        <h2>
          {result < 0
            ? "Předpokládaná vratka"
            : result > 0
              ? "Předpokládaný doplatek"
              : "Vypořádáno"}
        </h2>
        <strong className="settlement-value">
          {czk.format(Math.abs(result))}
        </strong>
        <p>
          Výpočet je orientační do konečného schválení poskytovatelem dotace.
        </p>
      </div>
      <dl>
        <dt>Schválené způsobilé výdaje</dt>
        <dd>{czk.format(Number(data.eligible_total))}</dd>
        <dt>Nárok na prostředky poskytovatele</dt>
        <dd>{czk.format(Number(data.provider_entitlement))}</dd>
        <dt>Dosud přijaté platby</dt>
        <dd>{czk.format(Number(data.net_received))}</dd>
      </dl>
    </section>
  );
}
function Dashboard() {
  const { id = "" } = useParams();
  const p = useQuery({
    queryKey: ["project", id],
    queryFn: () => api<Project>(`/projects/${id}`),
  });
  const d = useQuery({
    queryKey: ["dashboard", id],
    queryFn: () => api<any>(`/projects/${id}/dashboard`),
  });
  if (!p.data) return <main>Načítám…</main>;
  const x = d.data;
  return (
    <main>
      <Link to="/">← Projekty</Link>
      <div className="title">
        <div>
          <small>{p.data.project_code}</small>
          <h1>{p.data.project_name}</h1>
          <p>{p.data.recipient_name}</p>
        </div>
        <span className="badge">{p.data.status}</span>
      </div>
      {x && (
        <section className="metrics">
          <article>
            <small>CELKOVÝ ROZPOČET</small>
            <InfoTip text="Celkové způsobilé výdaje podle aktuálně platné verze rozpočtu." />
            <strong>{czk.format(x.total_budget)}</strong>
          </article>
          <article>
            <small>SCHVÁLENÉ ČERPÁNÍ</small>
            <InfoTip text="Součet způsobilých výdajů schválených v dosud nahraných a odevzdaných žádostech o platbu. Úvodní záloha se nezapočítává." />
            <strong>{czk.format(x.approved_spending)}</strong>
          </article>
          <article className={x.remaining < 0 ? "negative" : ""}>
            <small>ZŮSTATEK</small>
            <InfoTip text="Nevyčerpaná část aktuálního rozpočtu: celkový rozpočet minus schválené čerpání." />
            <strong>{czk.format(x.remaining)}</strong>
          </article>
          <article>
            <small>ČERPÁNÍ</small>
            <InfoTip text="Podíl schváleného čerpání na celkových způsobilých výdajích projektu." />
            <strong>{pct.format(x.percentage)} %</strong>
          </article>
          <article>
            <small>VZNIKLÝ PAUŠÁL</small>
            <InfoTip text="Nárok na paušální nepřímé náklady vypočtený ze schválených přímých výdajů a sazby paušálu." />
            <strong>{czk.format(x.entitlement)}</strong>
          </article>
          <article>
            <small>SKUTEČNĚ UTRACENÝ PAUŠÁL</small>
            <InfoTip text="Skutečná kumulativní útrata nepřímých nákladů ručně zadaná podle účetnictví." />
            <strong>{czk.format(x.spent)}</strong>
          </article>
          <article className={x.available < 0 ? "negative" : ""}>
            <small>DOSTUPNÝ PAUŠÁL</small>
            <InfoTip text="Dosud vzniklý paušální nárok minus skutečně utracený paušál." />
            <strong>{czk.format(x.available)}</strong>
            <div className="metric-detail">
              <span>Max. výše paušálu bez spolufinancování</span>
              <b>{czk.format(x.lump_sum_without_cofinancing)}</b>
            </div>
          </article>
          <article className="cofinancing">
            <small>
              SPOLUFINANCOVÁNÍ {pct.format(x.own_funding_rate * 100)} %
            </small>
            <InfoTip text="Vlastní podíl příjemce na dosud schválených způsobilých výdajích, rozdělený na přímé a nepřímé náklady." />
            <div>
              <span>Přímé náklady</span>
              <b>{czk.format(x.direct_cofinancing)}</b>
            </div>
            <div>
              <span>Nepřímé náklady</span>
              <b>{czk.format(x.indirect_cofinancing)}</b>
            </div>
            <div className="cofinancing-total">
              <span>Celkem</span>
              <b>{czk.format(x.cofinancing_total)}</b>
            </div>
          </article>
        </section>
      )}
      <FinalSettlement id={id} />
      <BudgetOverview id={id} periodCount={p.data.total_monitoring_periods} projectCode={p.data.project_code} projectName={p.data.project_name} />
      <LumpSumSpending id={id} />
      <PaymentRequests id={id} />
    </main>
  );
}
export function App() {
  return (
    <AuthGate>
      <Nav />
      <Routes>
        <Route path="/" element={<Projects />} />
        <Route path="/novy" element={<NewProject />} />
        <Route path="/projekty/:id" element={<Dashboard />} />
      </Routes>
    </AuthGate>
  );
}
