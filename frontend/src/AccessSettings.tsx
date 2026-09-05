import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";

type Project = { project_id: string; project_name: string; recipient_name: string };
type Rule = { email: string; role: "admin" | "editor" | "user"; active: boolean; scope: "all" | "projects" | "recipient"; project_ids: string[]; recipient_name: string; visible_project_ids?: string[] };
const empty: Rule = { email: "", role: "user", active: true, scope: "projects", project_ids: [], recipient_name: "" };
export function AccessSettings() {
  const qc = useQueryClient();
  const { data, error } = useQuery({ queryKey: ["access-settings"], queryFn: () => api<{ users: Rule[]; projects: Project[] }>("/admin/access") });
  const [form, setForm] = useState<Rule>(empty), [message, setMessage] = useState(""), [busy, setBusy] = useState(false);
  async function save(event: React.FormEvent) {
    event.preventDefault(); setMessage(""); setBusy(true);
    try {
      await api(`/admin/access/${encodeURIComponent(form.email.trim())}`, { method: "PUT", body: JSON.stringify(form) });
      await qc.invalidateQueries({ queryKey: ["access-settings"] });
      setMessage("Přístup uložen. Změna platí pro další požadavky uživatele bez nasazování nové verze.");
    } catch (e) { setMessage(e instanceof Error ? e.message : "Uložení se nepodařilo."); }
    finally { setBusy(false); }
  }
  if (error) return <main><section className="panel alert">Správa přístupů je dostupná pouze administrátorovi.</section></main>;
  if (!data) return <main>Načítám přístupy…</main>;
  return <main><section className="panel access-settings"><h1>Přístupy uživatelů</h1><p>Nový uživatel má přístup pouze k výslovně vybraným projektům. Správce vidí všechny projekty.</p>
    <div className="table-wrap"><table><thead><tr><th>E-mail</th><th>Role</th><th>Stav</th><th>Viditelné projekty</th><th /></tr></thead><tbody>{data.users.map(user => <tr key={user.email}><td>{user.email}</td><td>{user.role}</td><td>{user.active ? "Povolen" : "Zakázán"}</td><td>{data.projects.filter(p => user.visible_project_ids?.includes(p.project_id)).map(p => p.project_name).join("; ") || "Žádné"}</td><td><button className="secondary" onClick={() => setForm(user)}>Upravit</button></td></tr>)}</tbody></table></div>
    <p>Je-li Google OAuth aplikace v testovacím režimu, je potřeba účet povolit také mezi testovacími uživateli v Google Cloud.</p>
    <h2>{form.email ? "Upravit přístup" : "Přidat uživatele"}</h2><form onSubmit={save} className="access-form">
      <label>E-mail Google účtu<input type="email" required value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} /></label>
      <label>Role<select value={form.role} onChange={e => setForm({ ...form, role: e.target.value as Rule["role"] })}><option value="user">Pouze prohlížení</option><option value="editor">Editor podkladů</option><option value="admin">Administrátor všech projektů</option></select></label>
      <label><input type="checkbox" checked={form.active} onChange={e => setForm({ ...form, active: e.target.checked })} /> Povolit přihlášení</label>
      {form.role !== "admin" && <><label>Rozsah<select value={form.scope} onChange={e => setForm({ ...form, scope: e.target.value as Rule["scope"] })}><option value="projects">Vybrané projekty</option><option value="recipient">Projekty jednoho příjemce</option><option value="all">Všechny projekty</option></select></label>
      {form.scope === "recipient" && <label>Příjemce<select value={form.recipient_name} required onChange={e => setForm({ ...form, recipient_name: e.target.value })}><option value="">Vyberte příjemce</option>{[...new Set(data.projects.map(p => p.recipient_name))].map(name => <option key={name}>{name}</option>)}</select></label>}
      {form.scope === "projects" && <fieldset><legend>Povolené projekty</legend>{data.projects.map(p => <label key={p.project_id}><input type="checkbox" checked={form.project_ids.includes(p.project_id)} onChange={e => setForm({ ...form, project_ids: e.target.checked ? [...form.project_ids, p.project_id] : form.project_ids.filter(id => id !== p.project_id) })} /> {p.project_name}</label>)}</fieldset>}</>}
      <button disabled={busy}>{busy ? "Ukládám…" : "Uložit přístup"}</button><button type="button" className="secondary" onClick={() => setForm(empty)}>Nový uživatel</button>
      {message && <p role="status">{message}</p>}
    </form>
  </section></main>;
}
