export type Sd2Entry = {
  sd2_entry_id?: string; monitoring_period: number; month: string; budget_item_code: string;
  gross_wage: number; employer_contributions: number; other_with_contributions: number;
  other_without_contributions: number; payment_date?: string | null; external_id?: string;
  subject_id?: string; last_name?: string; first_name?: string;
  employment_type?: "Smlouva" | "DPC" | "DPP" | "DPPDo" | "DPPNad" | null;
  work_time_fund?: number; project_hours?: number; description?: string;
  source_file_name?: string; source_sha256?: string; source_key?: string;
};
export const normalizePerson = (value: string) => value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
export const entryPerson = (entry: Sd2Entry) => `${entry.first_name || ""} ${entry.last_name || ""}`.trim();
const personKey = (name: string) => {
  const words = normalizePerson(name).split(" ").filter(word => word && !["bc", "mgr", "ing", "mudr", "judr", "phd", "phdr", "dis"].includes(word));
  return words.length ? `${words[0]} ${words[words.length - 1]}` : "";
};
// The settings may use a shortened double surname. Use the same matching rule
// in the grid, editor and merge; never render one row and save to another.
export const samePerson = (a: string, b: string) => personKey(a) === personKey(b);
export const entryKey = (entry: Sd2Entry) => `${entry.budget_item_code}|${entry.month}|${personKey(entryPerson(entry))}`;
export function mergePayroll(existing: Sd2Entry[], incoming: Sd2Entry[]) {
  const keys = new Set(incoming.map(entryKey));
  // Replacing one person's slip must never remove their colleagues' records.
  return [...existing.filter(entry => !keys.has(entryKey(entry))), ...incoming.map(entry => {
    const old = existing.find(saved => entryKey(saved) === entryKey(entry));
    return { ...entry, sd2_entry_id: old?.sd2_entry_id, external_id: old?.external_id };
  })];
}
function ratio(value: number | undefined): [bigint, bigint] {
  const [mantissa, exponent = "0"] = String(value || 0).toLowerCase().split("e");
  const [whole, fraction = ""] = mantissa.split(".");
  const scale = fraction.length - Number(exponent);
  const integer = BigInt(whole + fraction);
  return scale >= 0 ? [integer, 10n ** BigInt(scale)] : [integer * 10n ** BigInt(-scale), 1n];
}
export function projectWage(entry: Pick<Sd2Entry, "gross_wage" | "work_time_fund" | "project_hours">) {
  if (Number(entry.work_time_fund || 0) <= 0) return Number(entry.gross_wage);
  const [gross, grossScale] = ratio(entry.gross_wage);
  const [fund, fundScale] = ratio(entry.work_time_fund);
  const [hours, hoursScale] = ratio(entry.project_hours);
  const numerator = gross * hours * fundScale * 100n;
  const denominator = grossScale * hoursScale * fund;
  const sign = numerator < 0 ? -1n : 1n;
  const absolute = numerator * sign;
  let cents = absolute / denominator;
  const remainder = absolute % denominator;
  // Match Decimal.quantize on the server (half-even), including exact ties.
  if (remainder * 2n > denominator || (remainder * 2n === denominator && cents % 2n !== 0n)) cents++;
  return Number(cents * sign) / 100;
}
export function projectAmount(entry: Sd2Entry) {
  return projectWage(entry) + Number(entry.employer_contributions) + Number(entry.other_with_contributions) + Number(entry.other_without_contributions);
}

export function incompleteXmlFields(entries: Sd2Entry[]) {
  return entries.flatMap(entry => {
    if (![entry.gross_wage, entry.employer_contributions, entry.other_with_contributions, entry.other_without_contributions].some(Number)) return [];
    const missing = [];
    if (!entry.subject_id?.trim()) missing.push("IČ subjektu");
    if (!entry.first_name?.trim() || !entry.last_name?.trim()) missing.push("jméno pracovníka");
    if (!entry.payment_date) missing.push("datum úhrady");
    if (!Number(entry.work_time_fund)) missing.push("fond hodin");
    if (!entry.employment_type) missing.push("pracovní vztah");
    if (!entry.description?.trim()) missing.push("popis výdaje");
    return missing.length ? [`${entry.budget_item_code}, ${entry.month.slice(0, 7)}, ${entryPerson(entry) || "bez jména"}: ${missing.join(", ")}`] : [];
  });
}
type Row = { code: string; employeeName: string; key: string };
export function buildEntries(source: Sd2Entry[], rows: Row[], months: string[], changes: Record<string, string | number>, period: number, subject: string, fixedSubject: string) {
  const fields = ["gross_wage", "employer_contributions", "other_with_contributions", "other_without_contributions", "payment_date"] as const;
  const relation = (code: string, previous?: Sd2Entry["employment_type"]) => code.startsWith("1.1.2.") ? "DPC" : code.startsWith("1.1.3.") ? (previous === "DPPDo" || previous === "DPPNad" ? previous : "DPP") : "Smlouva";
  const matches = (entry: Sd2Entry, row: Row) => entry.budget_item_code === row.code && (!row.employeeName || samePerson(entryPerson(entry), row.employeeName));
  const apply = (entry: Sd2Entry, row?: Row): Sd2Entry => {
    const next = { ...entry, subject_id: fixedSubject || entry.subject_id || subject, employment_type: relation(entry.budget_item_code, entry.employment_type) } as Sd2Entry;
    if (row) for (const field of fields) {
      const changed = changes[`${row.key}|${entry.month}|${field}`];
      if (changed != null) {
        if (field === "payment_date") next.payment_date = String(changed) || null;
        else next[field] = Number(changed || 0);
      }
    }
    return next;
  };
  const entries = source.map(entry => apply(entry, rows.find(row => matches(entry, row))));
  for (const row of rows) for (const month of months) {
    if (source.some(entry => entry.month === month && matches(entry, row))) continue;
    const edited = fields.some(field => changes[`${row.key}|${month}|${field}`] != null);
    // Initial blank grid remains editable; after import add only edited missing
    // cells, not hundreds of empty XML records.
    if (source.length && !edited) continue;
    const [first_name = "", ...surname] = row.employeeName.trim().split(/\s+/);
    entries.push(apply({ monitoring_period: period, month, budget_item_code: row.code, first_name, last_name: surname.join(" "),
      gross_wage: 0, employer_contributions: 0, other_with_contributions: 0, other_without_contributions: 0,
      work_time_fund: 0, project_hours: 0, description: "" }, row));
  }
  return entries;
}
