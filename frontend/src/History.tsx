import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";

type Snapshot = { snapshot_id: string; created_at: string; created_by: string; action: string; entry_count: number };
export function PeriodHistory({ id, period, revision, dirty, onRestored }: { id: string; period: number; revision?: string; dirty: boolean; onRestored: () => Promise<void> }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const { data = [], error: loadError } = useQuery({ queryKey: ["sd2-history", id, period], queryFn: () => api<Snapshot[]>(`/projects/${id}/sd2-history?period=${period}`) });
  const { data: me } = useQuery({ queryKey: ["me"], queryFn: () => api<{ role: string }>("/me") });
  async function restore(row: Snapshot) {
    if (!window.confirm(`Obnovit stav PŘED akcí „${row.action}“ z ${new Date(row.created_at + "Z").toLocaleString("cs-CZ")}? Současný uložený stav bude nejprve zálohován.${dirty ? " Neuložené změny budou zahozeny." : ""}`)) return;
    setError(""); setBusy(true);
    try {
      await api(`/projects/${id}/sd2-history/${row.snapshot_id}/restore?period=${period}`, { method: "POST", body: JSON.stringify({ revision }) });
      await onRestored();
    } catch (e) { setError(e instanceof Error ? e.message : "Obnova se nepodařila."); }
    finally { setBusy(false); }
  }
  return <details className="period-history"><summary>Historie období a obnova ({data.length})</summary>
    <p>Ukládá se stav před změnou. Historie vzniká od nasazení této funkce; obnovují se údaje SD2, nikoli soubory na Disku.</p>
    {error && <div className="alert">{error}</div>}
    {loadError && <div className="alert">Historii se nepodařilo načíst. Zkuste to znovu po obnovení spojení.</div>}
    {data.length === 0 ? <p>Zatím nejsou uložené žádné předchozí stavy.</p> : <ul>{data.map(row => <li key={row.snapshot_id}>
      {new Date(row.created_at + "Z").toLocaleString("cs-CZ")} – {row.action}, {row.entry_count} záznamů, {row.created_by} { ["admin", "editor"].includes(me?.role || "") && <button type="button" className="secondary" disabled={busy || !revision} onClick={() => restore(row)}>Obnovit stav před změnou</button>}
    </li>)}</ul>}
  </details>;
}
type Log = { import_id: string; project_id: string; created_at: string; created_by: string; import_type: string; source_file_name: string; message: string };
export function ImportHistory({ id }: { id: string }) {
  const { data = [], error } = useQuery({ queryKey: ["import-log"], queryFn: () => api<Log[]>("/import-log") });
  const rows = data.filter(row => row.project_id === id).slice().reverse();
  return <section className="panel import-history"><details><summary>Historie importů a změn ({rows.length})</summary>
    {error && <div className="alert">Historii se nepodařilo načíst.</div>}
    {!rows.length && <p>Nové importy a změny se budou zaznamenávat od nasazení této funkce.</p>}
    <div className="table-wrap"><table><thead><tr><th>Kdy</th><th>Kdo</th><th>Akce</th><th>Soubory / změna</th></tr></thead><tbody>{rows.map(row => <tr key={row.import_id}><td>{new Date(row.created_at + "Z").toLocaleString("cs-CZ")}</td><td>{row.created_by}</td><td>{row.import_type}</td><td>{row.message}<small>{row.source_file_name}</small></td></tr>)}</tbody></table></div>
  </details></section>;
}
