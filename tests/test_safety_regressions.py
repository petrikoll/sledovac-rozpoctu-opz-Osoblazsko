from copy import deepcopy
from datetime import date
from decimal import Decimal as D
from io import BytesIO
import threading
import zipfile

import pytest
from fastapi.testclient import TestClient
from app import main as m
from app.models import BudgetAnalysis, BudgetItem, Project, PaymentRequest, Sd2MonthlyEntry, LumpSumEntry
from app.repository import GoogleSheetsRepository, InMemoryRepository, SHEETS
from app.sd2_history import revision, snapshot_rows, snapshots


@pytest.fixture
def isolated(monkeypatch):
    previous = deepcopy(m.repo.__dict__)
    m.repo.__dict__.update(InMemoryRepository().__dict__)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "synthetic-client")
    monkeypatch.setenv("ALLOWED_EMAILS", "admin@example.invalid,katfol@email.cz,reader@example.invalid")
    monkeypatch.setattr(m, "google_repo", None)
    monkeypatch.setattr(m, "user_roles", {"admin@example.invalid": "admin", "katfol@email.cz": "editor", "reader@example.invalid": "user"})
    monkeypatch.setattr(m.id_token, "verify_oauth2_token", lambda token, *_: {"email": token, "email_verified": True})
    client = TestClient(m.app, raise_server_exceptions=False)
    client.headers["Authorization"] = "Bearer admin@example.invalid"
    yield client
    m.repo.__dict__.clear()
    m.repo.__dict__.update(previous)


def make_project(name="Test", recipient="Other recipient"):
    p = Project(project_code="TEST", project_name=name, recipient_name=recipient, total_budget=1000000, public_funding_rate=1, active_budget_version_id="budget")
    m.repo.save_project(p)
    item = BudgetItem(code="1.1.1.1", name="Salary", level=4, source_row_number=1, total_amount=1000000)
    analysis = BudgetAnalysis(items=[item], sha256="test", file_name="test.xlsx", total_amount=1000000, lump_sum_rate=D("0.4"), lump_sum_base_code="1.1", leaf_count=1, summary_count=0)
    m.repo.budgets[p.project_id] = [{"version_id": "budget", "analysis": analysis, "sha256": "test"}]
    return p


def wage(**values):
    return Sd2MonthlyEntry(**{"monitoring_period": 1, "month": date(2026, 6, 1), "budget_item_code": "1.1.1.1", "gross_wage": 60000, "work_time_fund": 160, "project_hours": 32, "employer_contributions": 4056, **values})


def payment(version=1, amount=100, state="Proplacená", number="TEST/2"):
    return PaymentRequest(project_code="TEST", project_name="Test", recipient_name="Other recipient", sequence_number=2, request_number=number, request_version=version, request_type="ANTE", state=state, processing_state=state, is_final_payment=False, is_advance_payment=False, declared_direct_costs=amount, approved_direct_costs=amount, public_payment=amount, approved_total=amount, source_sha256=f"{number}-{version}", source_file_name="test.pdf")


@pytest.mark.parametrize("suffix", ["", "/dashboard", "/lump-sum-spending", "/cofinancing", "/sd2-monthly?period=1", "/sd2-state?period=1", "/sd2-history?period=1", "/budget-status", "/final-settlement"])
def test_restricted_user_cannot_read_other_project(isolated, suffix):
    p = make_project()
    response = isolated.get(f"/api/projects/{p.project_id}{suffix}", headers={"Authorization": "Bearer katfol@email.cz"})
    assert response.status_code == 404


def test_restricted_editor_cannot_read_payment_or_mutate_other_project(isolated):
    p = make_project()
    pay = payment(); m.repo.payments[p.project_id] = [pay]
    entry = LumpSumEntry(monitoring_period="1", entry_date=date(2026, 6, 1), entry_mode="period", entered_amount=100)
    m.repo.lump_entries[p.project_id] = [entry]
    headers = {"Authorization": "Bearer katfol@email.cz"}
    assert isolated.get(f"/api/projects/{p.project_id}/payment-requests/{pay.payment_request_id}", headers=headers).status_code == 404
    url = f"/api/projects/{p.project_id}/lump-sum-spending/{entry.lump_sum_entry_id}"
    assert isolated.patch(url, headers=headers, json={"entered_amount": 999}).status_code == 404
    assert isolated.delete(url, headers=headers).status_code == 404
    assert m.repo.lump_entries[p.project_id][0].entered_amount == 100


def test_budget_changes_are_admin_only_even_for_allowed_editor(isolated):
    p = make_project(recipient="Osoblažský cech, z.ú.")
    for endpoint in ("budgets/import", "budget-change/import", "change-proposals/generate"):
        assert isolated.post(f"/api/projects/{p.project_id}/{endpoint}", json={}, headers={"Authorization": "Bearer katfol@email.cz"}).status_code == 403


def test_project_portion_not_whole_employment_is_counted(isolated):
    p = make_project(); m.repo.sd2_entries[p.project_id] = [wage()]
    assert wage().total_amount == D("16056")
    data = isolated.get(f"/api/projects/{p.project_id}/budget-status").json()
    assert D(str(data[0]["cumulative_spent"])) == D("16056")
    assert wage(work_time_fund=0, project_hours=0).total_amount == 64056  # legacy project-only input


def test_payment_revisions_do_not_double_count(isolated):
    p = make_project()
    for version, amount in ((2, 120), (1, 100)):
        token = f"review-{version}"
        m.analyses[token] = {"kind": "payment", "project_id": p.project_id, "result": payment(version, amount)}
        assert isolated.post(f"/api/projects/{p.project_id}/payment-requests/import", json={"token": token}).status_code == 200
    data = isolated.get(f"/api/projects/{p.project_id}/dashboard").json()
    assert data["approved_spending"] == 120
    result = isolated.get(f"/api/projects/{p.project_id}/final-settlement").json()
    assert result["net_received"] == 120
    assert sum(row["active_revision"] for row in isolated.get(f"/api/projects/{p.project_id}/payment-requests").json()) == 1


def test_pending_not_counted_as_approved_and_negated_status_not_approved(isolated):
    p = make_project()
    m.repo.payments[p.project_id] = [payment(), payment(1, 50, "Zaregistrovaná", "TEST/3")]
    assert isolated.get(f"/api/projects/{p.project_id}/dashboard").json()["approved_spending"] == 100
    assert not m._payment_is_approved(payment(state="Neschválená"))


def test_history_restore_and_concurrent_edit_protection(isolated):
    p = make_project(); old = wage(); m.repo.sd2_entries[p.project_id] = [old]
    state = isolated.get(f"/api/projects/{p.project_id}/sd2-state?period=1").json()
    body = {"entries": [{**old.model_dump(mode="json"), "gross_wage": "70000"}], "revision": state["revision"]}
    url = f"/api/projects/{p.project_id}/sd2-monthly"
    assert isolated.put(url, json=body).status_code == 200
    assert isolated.put(url, json=body).status_code == 409
    history = isolated.get(f"/api/projects/{p.project_id}/sd2-history?period=1").json()
    assert len(history) == 1
    assert "entries" not in history[0]
    latest = isolated.get(f"/api/projects/{p.project_id}/sd2-state?period=1").json()
    restored = isolated.post(f"/api/projects/{p.project_id}/sd2-history/{history[0]['snapshot_id']}/restore?period=1", json={"revision": latest["revision"]})
    assert restored.status_code == 200
    assert m.repo.sd2_entries[p.project_id][0].gross_wage == old.gross_wage
    assert len(snapshots(m.repo.sd2_history, p.project_id, 1)) == 2


def test_snapshot_chunk_roundtrip():
    entries = [wage(description="ě" * 2000) for _ in range(8)]
    rows = snapshot_rows("p", 1, entries, {"email": "admin"}, "save")
    assert len(rows) > 1 and max(len(r["payload"]) for r in rows) <= 8000
    assert snapshots(rows, "p", 1)[0]["entries"] == [entry.model_dump(mode="json") for entry in entries]


def test_restore_empty_snapshot_keeps_attachments(isolated):
    p = make_project(); entry = wage()
    m.repo.sd2_entries[p.project_id] = [entry]
    attachment = {"monitoring_period": 1, "attachment_id": "keep", "file_name": "proof.zip"}
    m.repo.sd2_attachments[p.project_id] = [attachment]
    history = snapshot_rows(p.project_id, 1, [], {"email": "admin"}, "First import")
    m.repo.sd2_history.extend(history)
    result = isolated.post(f"/api/projects/{p.project_id}/sd2-history/{history[0]['snapshot_id']}/restore?period=1", json={"revision": revision([entry])})
    assert result.status_code == 200
    assert m.repo.sd2_entries[p.project_id] == []
    assert m.repo.sd2_attachments[p.project_id] == [attachment]


def test_zip_identical_files_are_skipped_but_conflicting_revisions_block(isolated, monkeypatch):
    p = make_project("Řešení předluženosti na severním Osoblažsku", "Osoblažský cech, z.ú.")
    m.repo.project_schedules[p.project_id] = {"periods": [{"monitoring_period": 1, "start_month": "2026-01-01", "end_month": "2026-07-01"}]}
    m.repo.worker_assignments[p.project_id] = [{"employee_name": "Synthetic Worker", "budget_item_code": "1.1.1.1"}]
    row = {**wage().model_dump(), "source_key": "test", "full_name": "Synthetic Worker", "first_name": "Synthetic", "last_name": "Worker", "performance_code": "17P ŘPSO", "contract_name": "HPP", "month": "2026-06-01", "payment_date": "2026-07-08", "worked_hours": 32}
    monkeypatch.setattr(m, "parse_payroll_slips", lambda _: [dict(row)])
    def archive(second):
        data = BytesIO()
        with zipfile.ZipFile(data, "w") as z:
            z.writestr("one.pdf", b"test"); z.writestr("two.pdf", second)
        return data.getvalue()
    response = isolated.post("/api/payroll-batch/import", files={"file": ("test.zip", archive(b"test"), "application/zip")})
    assert response.status_code == 200
    assert response.json()["imported_entries"] == 1
    assert response.json()["duplicates"] == ["two.pdf"]
    bad = isolated.post("/api/payroll-batch/analyze", files={"file": ("test.zip", archive(b"revised"), "application/zip")}).json()
    assert bad["ready_groups"] == 0
    assert any("Více podkladů" in issue for issue in bad["groups"][0]["issues"])


def test_access_can_be_managed_without_environment_change(isolated):
    yes, no = make_project("Allowed"), make_project("Denied")
    body = {"email": "new@example.invalid", "role": "editor", "active": True, "scope": "projects", "project_ids": [yes.project_id]}
    assert isolated.put("/api/admin/access/new@example.invalid", json=body).status_code == 200
    headers = {"Authorization": "Bearer new@example.invalid"}
    assert [p["project_id"] for p in isolated.get("/api/projects", headers=headers).json()] == [yes.project_id]
    assert isolated.get(f"/api/projects/{no.project_id}/dashboard", headers=headers).status_code == 404
    assert isolated.put("/api/admin/access/new@example.invalid", json={**body, "active": False}).status_code == 200
    assert isolated.get("/api/projects", headers=headers).status_code == 403


def test_unverified_google_address_is_not_accepted(isolated, monkeypatch):
    monkeypatch.setattr(m.id_token, "verify_oauth2_token", lambda *_: {"email": "admin@example.invalid", "email_verified": False})
    assert isolated.get("/api/projects").status_code == 401


def test_reading_partner_xml_does_not_change_saved_records_or_revision(isolated):
    p = make_project("Řešení oblasti dluhové problematiky na území MAS")
    entry = wage(subject_id="", employment_type=None)
    m.repo.sd2_entries[p.project_id] = [entry]
    before = entry.model_dump()
    state = isolated.get(f"/api/projects/{p.project_id}/sd2-state?period=1").json()
    assert state["entries"][0]["subject_id"] == m.MAS_PARTNER_SUBJECT_ID
    assert state["revision"] == revision([entry])
    assert isolated.get(f"/api/projects/{p.project_id}/sd2-xml?period=1").status_code == 200
    assert entry.model_dump() == before
    assert isolated.put(f"/api/projects/{p.project_id}/sd2-monthly", json=state).status_code == 200


def test_invalid_or_duplicate_sd2_input_keeps_old_state(isolated):
    p = make_project(); old = wage(); m.repo.sd2_entries[p.project_id] = [old]
    url = f"/api/projects/{p.project_id}/sd2-monthly"
    for entries in ([{**old.model_dump(mode="json"), "project_hours": 161}],
                    [{**old.model_dump(mode="json"), "gross_wage": "not-a-number"}],
                    [old.model_dump(mode="json"), old.model_dump(mode="json")]):
        assert isolated.put(url, json={"entries": entries, "revision": revision([old])}).status_code == 422
        assert m.repo.sd2_entries[p.project_id] == [old]
        assert not m.repo.sd2_history


def test_mosty_multiple_components_need_explicit_confirmation(isolated):
    rows = [{"source_key": str(i), "first_name": "Silvie", "last_name": "Malíková", "month": "2026-06-01",
             "contract_name": "PS", "component_code": "M01", "component_occurrence": i,
             "component_amount": amount, "contract_gross": 50000, "work_time_fund": 140.8,
             "full_time_fund": 176} for i, amount in ((1, 40000), (2, 10000))]
    result = m._mosty_payroll_rows(rows, {"1.1.1.3"})
    assert len(result) == 1  # still offered when M01 is not third
    assert result[0]["requires_component_confirmation"]
    assert result[0]["selected_component_key"] == ""
    assert len(result[0]["component_options"]) == 2
    single = m._mosty_payroll_rows(rows[:1], {"1.1.1.3"})
    assert single[0]["requires_component_confirmation"]
    assert single[0]["selected_component_key"] == ""


def test_historical_dpp_type_is_preserved_but_not_used_for_2026():
    assert m.sd2_employment_type("1.1.3.1", "DPPDo", date(2024, 12, 1)) == "DPPDo"
    assert m.sd2_employment_type("1.1.3.1", "DPPDo", date(2026, 1, 1)) == "DPP"


def test_missing_production_storage_is_not_reported_as_saved(isolated, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert isolated.get("/api/health").status_code == 503
    assert isolated.post("/api/projects", json={"project_name": "Never stored"}).status_code == 503
    assert not m.repo.project_data


def test_uncertain_commit_reloads_authoritative_storage_before_next_read(isolated, monkeypatch):
    p = make_project(); old = wage(); m.repo.sd2_entries[p.project_id] = [old]
    committed = wage(**{**old.model_dump(), "gross_wage": 70000})
    class UncertainStorage:
        reloads = 0
        def delete_record(self, *args):
            from app.transactions import stage
            stage("delete", *args, False)
        def append_records(self, *args):
            from app.transactions import stage
            stage("append", *args)
        def commit(self, _operations):
            raise TimeoutError("The remote server may have committed this write")
        def hydrate(self, target):
            self.reloads += 1
            target.__dict__.update(deepcopy(m.repo.__dict__))
            target.sd2_entries[p.project_id] = [committed]
    storage = UncertainStorage()
    monkeypatch.setattr(m, "google_repo", storage)
    result = isolated.put(f"/api/projects/{p.project_id}/sd2-monthly", json={"entries": [committed.model_dump(mode="json")], "revision": revision([old])})
    assert result.status_code == 503
    assert m.repo.sd2_entries[p.project_id][0].gross_wage == 60000
    refreshed = isolated.get(f"/api/projects/{p.project_id}/sd2-state?period=1")
    assert refreshed.status_code == 200
    assert D(refreshed.json()["entries"][0]["gross_wage"]) == 70000
    assert storage.reloads == 1


class Call:
    def __init__(self, value): self.value = value
    def execute(self): return self.value() if callable(self.value) else self.value


class SheetsAPI:
    def __init__(self):
        self.tables = {key: [] for key in SHEETS}
        self.ids = {key: i for i, key in enumerate(SHEETS)}
        self.commits = []
        self.fail = False
    def values(self): return self
    def get(self, **kwargs):
        if "range" in kwargs:
            return Call({"values": deepcopy(self.tables[kwargs["range"].split("!")[0].strip("'")])})
        return Call({"sheets": [{"properties": {"title": key, "sheetId": value}} for key, value in self.ids.items()]})
    def batchUpdate(self, **kwargs):
        def run():
            self.commits.append(kwargs["body"]["requests"])
            if self.fail: raise RuntimeError("simulated atomic failure")
            tables = deepcopy(self.tables)
            names = {value: key for key, value in self.ids.items()}
            for request in kwargs["body"]["requests"]:
                if "appendCells" in request:
                    op = request["appendCells"]
                    tables[names[op["sheetId"]]].extend([[next(iter(c["userEnteredValue"].values())) for c in row["values"]] for row in op["rows"]])
                elif "deleteDimension" in request:
                    op = request["deleteDimension"]["range"]
                    del tables[names[op["sheetId"]]][op["startIndex"] - 1:op["endIndex"] - 1]
                elif "updateCells" in request:
                    op = request["updateCells"]; start = op["start"]
                    tables[names[start["sheetId"]]][start["rowIndex"] - 1][start["columnIndex"]] = next(iter(op["rows"][0]["values"][0]["userEnteredValue"].values()))
            self.tables = tables
            return {}
        return Call(run)


@pytest.mark.parametrize("fail", [False, True])
def test_sd2_save_is_one_atomic_batch_and_rolls_back_memory(isolated, monkeypatch, fail):
    p = make_project(); old = wage(); m.repo.sd2_entries[p.project_id] = [old]
    remote = object.__new__(GoogleSheetsRepository)
    remote.api, remote.id, remote._google_lock = SheetsAPI(), "test", threading.RLock()
    record = {**old.model_dump(mode="json"), "project_id": p.project_id}
    remote.api.tables["SD2_MESICE"] = [[record.get(k, "") for k in SHEETS["SD2_MESICE"]]]
    remote.api.fail = fail
    monkeypatch.setattr(m, "google_repo", remote)
    response = isolated.put(f"/api/projects/{p.project_id}/sd2-monthly", json={"entries": [{**old.model_dump(mode="json"), "gross_wage": 70000}], "revision": revision([old])})
    assert len(remote.api.commits) == 1
    assert response.status_code == (503 if fail else 200)
    assert m.repo.sd2_entries[p.project_id][0].gross_wage == (60000 if fail else 70000)
    persisted = remote.api.tables["SD2_MESICE"]
    assert len(persisted) == 1
    assert D(str(persisted[0][SHEETS["SD2_MESICE"].index("gross_wage")])) == (60000 if fail else 70000)
    assert bool(m.repo.sd2_history) is not fail
