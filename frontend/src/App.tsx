import { useEffect, useRef, useState } from "react";
import { Link, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { api, czk, downloadApi, pct } from "./api";
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
type Sd2Entry = { sd2_entry_id?: string; monitoring_period: number; month: string; budget_item_code: string; gross_wage: number; employer_contributions: number; other_with_contributions: number; other_without_contributions: number; payment_date?: string | null };
const CLIENT_ID =
  import.meta.env.VITE_GOOGLE_CLIENT_ID ||
  "812727560459-codfb0fu10agboif0lsjce3k6on4rj3d.apps.googleusercontent.com";
const DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file";
const SD2_DRIVE_FOLDER = "Dokumenty aplikace OPZ+";

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

async function uploadSd2ArchiveToUserDrive(file: File, projectId: string, period: number) {
  const accessToken = await requestDriveAccessToken();
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
function AuthGate({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState(() =>
    localStorage.getItem("opz_google_token"),
  );
  const button = useRef<HTMLDivElement>(null);
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
function Sd2MonthlyDialogNew({ id, period, onClose }: { id: string; period: number; onClose: () => void }) {
  const qc = useQueryClient(); const months = SD2_MONTHS[period - 1];
  const { data = [] } = useQuery({ queryKey: ["sd2-monthly", id, period], queryFn: () => api<Sd2Entry[]>(`/projects/${id}/sd2-monthly?period=${period}`) });
  const { data: attachments = [] } = useQuery({ queryKey: ["sd2-attachments", id, period], queryFn: () => api<any[]>(`/projects/${id}/sd2-attachments?period=${period}`) });
  const [changes, setChanges] = useState<Record<string, string | number>>({}); const [saving, setSaving] = useState(false); const [error, setError] = useState("");
  const read = (code: string, month: string, field: keyof Sd2Entry) => changes[`${code}|${month}|${field}`] ?? data.find(x => x.budget_item_code === code && x.month === month)?.[field] ?? (field === "payment_date" ? "" : 0);
  const set = (code: string, month: string, field: keyof Sd2Entry, value: string) => setChanges(current => ({ ...current, [`${code}|${month}|${field}`]: value }));
  async function save() { setSaving(true); setError(""); try { const entries: Sd2Entry[] = SD2_CODES.flatMap(code => months.map(month => { const old = data.find(x => x.budget_item_code === code && x.month === month); return { sd2_entry_id: old?.sd2_entry_id, monitoring_period: period, month, budget_item_code: code, gross_wage: Number(read(code, month, "gross_wage") || 0), employer_contributions: code === "1.1.3.1" ? 0 : Number(read(code, month, "employer_contributions") || 0), other_with_contributions: Number(read(code, month, "other_with_contributions") || 0), other_without_contributions: Number(read(code, month, "other_without_contributions") || 0), payment_date: String(read(code, month, "payment_date") || "") || null }; })); await api(`/projects/${id}/sd2-monthly`, { method: "PUT", body: JSON.stringify({ entries }) }); await qc.invalidateQueries({ queryKey: ["budget-status", id] }); onClose(); } catch (e) { setError(e instanceof Error ? e.message : "Podklad SD2 se nepodařilo uložit."); } finally { setSaving(false); } }
  async function upload(file: File) {
    setError("");
    try {
      const driveFileId = await uploadSd2ArchiveToUserDrive(file, id, period);
      await api(`/projects/${id}/sd2-attachments/record?period=${period}`, {
        method: "POST",
        body: JSON.stringify({ file_name: file.name, drive_file_id: driveFileId }),
      });
      await qc.invalidateQueries({ queryKey: ["sd2-attachments", id, period] });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Archiv se nepodařilo uložit.");
    }
  }
  return <div className="sd2-overlay" role="dialog" aria-modal="true"><section className="sd2-dialog"><div className="sd2-dialog-head"><div><h2>Podklad SD2 — {period}. období</h2><p>Měsíční údaje se zobrazí v příslušném období jako podklad před ŽoP.</p></div><div className="sd2-attachments"><label className="upload-button">Uložit výplatní lístky a výpisy z BÚ<input type="file" accept=".zip,.rar" onChange={e => e.target.files?.[0] && upload(e.target.files[0])} /></label>{attachments.map(x => <small key={x.attachment_id}>{x.file_name}</small>)}</div><button className="secondary" onClick={onClose}>Zavřít</button></div>{error && <div className="alert">{error}</div>}<div className="sd2-grid-wrap"><table className="sd2-grid"><thead><tr><th>Položka</th>{months.map(month => <th key={month}>{new Date(`${month}T00:00:00Z`).toLocaleDateString("cs-CZ", { month: "long", year: "numeric" })}</th>)}</tr></thead><tbody>{SD2_CODES.map(code => <tr key={code}><th><b>{code}</b><small>{SD2_ITEM_NAMES[code]}</small></th>{months.map(month => { const noContributions = code === "1.1.3.1"; return <td key={month}><label>Hrubá mzda / odměna<input type="number" step="0.01" value={read(code, month, "gross_wage")} onChange={e => set(code, month, "gross_wage", e.target.value)} /></label>{!noContributions && <label>Odvody zaměstnavatele<input type="number" step="0.01" value={read(code, month, "employer_contributions")} onChange={e => set(code, month, "employer_contributions", e.target.value)} /></label>}<label>Jiné výdaje s odvody<input type="number" step="0.01" value={read(code, month, "other_with_contributions")} onChange={e => set(code, month, "other_with_contributions", e.target.value)} /></label><label>Jiné výdaje bez odvodů<input type="number" step="0.01" value={read(code, month, "other_without_contributions")} onChange={e => set(code, month, "other_without_contributions", e.target.value)} /></label><label>Datum úhrady<input type="date" value={read(code, month, "payment_date")} onChange={e => set(code, month, "payment_date", e.target.value)} /></label></td>; })}</tr>)}</tbody></table></div><div className="sd2-save"><button onClick={save} disabled={saving}>{saving ? "Ukládám…" : "Uložit podklad SD2"}</button></div></section></div>;
}
function BudgetOverview({
  id,
  periodCount,
  activeVersionId,
  projectCode,
}: {
  id: string;
  periodCount: number;
  activeVersionId?: string | null;
  projectCode?: string;
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
  const [deleteError, setDeleteError] = useState("");
  const [sd2Period, setSd2Period] = useState<number | null>(null);
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
  return (
    <section className="panel wide budget-overview">
      <div className="budget-overview-head">
        <div>
          <h2>Čerpání rozpočtu po položkách</h2>
          <p>
            {historical
              ? `Historický pohled na verzi ${selectedIndex + 1}. Čerpání je promítnuto proti tehdejším částkám.`
              : `Schválené čerpání je rozdělené do ${periods.length} monitorovacích období. Pomlčka označuje období bez nahrané žádosti o platbu.`}
          </p>
        </div>
        <div className="budget-tools">
          <label className="version-select">
            Verze rozpočtu
            <select
              value={selectedVersion}
              onChange={(e) => setSelectedVersion(e.target.value)}
            >
              {versions.data?.map((version, index) => (
                <option key={version.version_id} value={version.version_id}>
                  {version.version_id === effectiveActive
                    ? `Aktuální verze ${index + 1}`
                    : index === 0
                      ? "Původní rozpočet"
                      : `Verze ${index + 1}`}
                </option>
              ))}
            </select>
          </label>
          <ImportBudget id={id} compact />
          <BudgetChange id={id} compact />
          {me.data?.role === "admin" && selectedVersion && (
            <button className="danger" type="button" onClick={removeSelectedVersion}>
              Smazat verzi
            </button>
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
                  className="period-col"
                  key={p}
                  onClick={() => projectCode === SD2_PROJECT_CODE && setSd2Period(Number(p))}
                  role={projectCode === SD2_PROJECT_CODE ? "button" : undefined}
                  title={`Monitorovací období ${p}`}
                >
                  {p}. období
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
      {sd2Period && <Sd2MonthlyDialogNew id={id} period={sd2Period} onClose={() => setSd2Period(null)} />}
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
      <BudgetOverview id={id} periodCount={p.data.total_monitoring_periods} projectCode={p.data.project_code} />
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
