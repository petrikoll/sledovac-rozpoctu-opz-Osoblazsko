import { expect, test } from "vitest";
import { buildEntries, mergePayroll, projectAmount, projectWage, incompleteXmlFields, type Sd2Entry } from "./sd2";

const entry = (values: Partial<Sd2Entry> = {}): Sd2Entry => ({
  sd2_entry_id: "saved-id", external_id: "external-id", monitoring_period: 1,
  month: "2026-05-01", budget_item_code: "1.1.1.1", first_name: "Jana", last_name: "Testová",
  gross_wage: 60000, employer_contributions: 4056, other_with_contributions: 0,
  other_without_contributions: 0, work_time_fund: 160, project_hours: 32, ...values,
});
const rows = [{ code: "1.1.1.1", employeeName: "Jana Testová", key: "jana" }];

test("ručně doplněný nový měsíc se uloží i po importu předchozího měsíce", () => {
  const result = buildEntries([entry()], rows, ["2026-05-01", "2026-06-01", "2026-07-01"],
    { "jana|2026-06-01|gross_wage": "12345" }, 1, "12345678", "");
  expect(result).toHaveLength(2);
  expect(result.find(row => row.month === "2026-06-01")?.gross_wage).toBe(12345);
  expect(result[0].sd2_entry_id).toBe("saved-id");
});

test("odvody u DPP nejsou při uložení vynulovány", () => {
  const result = buildEntries([entry({ budget_item_code: "1.1.3.1", employer_contributions: 5000 })],
    [{ ...rows[0], code: "1.1.3.1" }], ["2026-05-01"], {}, 1, "", "");
  expect(result[0].employer_contributions).toBe(5000);
  expect(result[0].employment_type).toBe("DPP");
});

test("nová páska nahradí jen jednoho pracovníka a zachová stabilní XML ID", () => {
  const colleague = entry({ sd2_entry_id: "colleague", first_name: "Petr" });
  const june = entry({ sd2_entry_id: "june", month: "2026-06-01" });
  const result = mergePayroll([entry(), colleague, june], [entry({ gross_wage: 70000, sd2_entry_id: undefined, external_id: undefined })]);
  expect(result).toHaveLength(3);
  expect(result).toContainEqual(colleague);
  expect(result).toContainEqual(june);
  expect(result.find(row => row.gross_wage === 70000)).toMatchObject({ sd2_entry_id: "saved-id", external_id: "external-id" });
});

test("import zachová neuložené ruční změny jiných měsíců", () => {
  const draft = buildEntries([entry()], rows, ["2026-05-01", "2026-06-01"],
    { "jana|2026-06-01|gross_wage": "1000" }, 1, "", "");
  const result = mergePayroll(draft, [entry({ gross_wage: 65000 })]);
  expect(result.find(row => row.month === "2026-06-01")?.gross_wage).toBe(1000);
});

test("výpočet zobrazuje pouze projektovou část mzdy včetně korekcí a odvodů", () => {
  expect(projectAmount(entry())).toBe(16056);
  expect(projectAmount(entry({ other_with_contributions: -100, other_without_contributions: 50 }))).toBe(16006);
  expect(projectAmount(entry({ work_time_fund: 0, project_hours: 0 }))).toBe(64056);
});

test("zkrácené dvojité příjmení se shoduje při zobrazení i uložení", () => {
  const source = entry({ first_name: "Petra", last_name: "Dlouhá Testová" });
  const result = buildEntries([source], [{ ...rows[0], employeeName: "Petra Testová" }], ["2026-05-01"],
    { "jana|2026-05-01|gross_wage": "70000" }, 1, "", "");
  expect(result).toHaveLength(1);
  expect(result[0].gross_wage).toBe(70000);
});

test("neúplné XML dostane upozornění, prázdné řádky ne", () => {
  expect(incompleteXmlFields([entry()])[0]).toContain("datum úhrady");
  expect(incompleteXmlFields([entry({ gross_wage: 0, employer_contributions: 0 })])).toEqual([]);
});

test("haléřové zaokrouhlení náhledu odpovídá backendu i na polovině haléře", () => {
  expect(projectWage(entry({ gross_wage: 1, work_time_fund: 40, project_hours: 1 }))).toBe(0.02);
  expect(projectWage(entry({ gross_wage: 1, work_time_fund: 40, project_hours: 3 }))).toBe(0.08);
  expect(projectWage(entry({ gross_wage: -1, work_time_fund: 40, project_hours: 1 }))).toBe(-0.02);
  expect(projectWage(entry({ gross_wage: 1927.12, work_time_fund: 176, project_hours: 17.6 }))).toBe(192.71);
});
