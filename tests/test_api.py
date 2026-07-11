from fastapi.testclient import TestClient
from decimal import Decimal
from app.main import app, repo

client = TestClient(app)


def test_health_and_project_crud():
    repo.project_data.clear()
    assert client.get("/api/health").status_code == 200
    payload = {"project_code": "CZ.TEST", "project_name": "Test", "recipient_name": "Příjemce"}
    created = client.post("/api/projects", json=payload)
    assert created.status_code == 201
    assert client.get("/api/projects").json()[0]["project_name"] == "Test"


def test_budget_two_phase_import_and_duplicate():
    project = client.post("/api/projects", json={"project_code": "CZ.B", "project_name": "B", "recipient_name": "P"}).json()
    with open("samples/Export_2026-07-11_084920.xlsx", "rb") as f:
        analyzed = client.post(f"/api/projects/{project['project_id']}/budgets/analyze", files={"file": ("budget.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert analyzed.status_code == 200
    imported = client.post(f"/api/projects/{project['project_id']}/budgets/import", json={"token": analyzed.json()["token"]})
    assert imported.status_code == 200


def test_admin_can_delete_budget_version():
    project = client.post("/api/projects", json={"project_code": "CZ.D", "project_name": "D", "recipient_name": "P"}).json()
    with open("samples/Export_2026-07-11_084920.xlsx", "rb") as f:
        analyzed = client.post(f"/api/projects/{project['project_id']}/budgets/analyze", files={"file": ("budget.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    version_id = client.post(f"/api/projects/{project['project_id']}/budgets/import", json={"token": analyzed.json()["token"]}).json()["version_id"]

    assert client.delete(f"/api/projects/{project['project_id']}/budgets/{version_id}").status_code == 204
    assert client.get(f"/api/projects/{project['project_id']}/budgets").json() == []
    updated = client.get(f"/api/projects/{project['project_id']}").json()
    assert updated["active_budget_version_id"] is None
    assert Decimal(updated["total_budget"]) == Decimal("0")


def test_hydration_ignores_payment_for_missing_project(monkeypatch):
    from app.repository import GoogleSheetsRepository, InMemoryRepository

    records = {
        "PROJEKT_UZIVATELE": [], "PROJEKTY": [], "POLOZKY_ROZPOCTU": [],
        "VERZE_ROZPOCTU": [], "RADKY_ZOP": [],
        "ZADOSTI_O_PLATBU": [{
            "payment_request_id": "orphan", "project_id": "missing",
            "sequence_number": "1", "request_number": "orphan/1",
        }],
        "UTRATA_PAUSALU": [], "SPOLUFINANCOVANI": [], "IMPORT_LOG": [],
    }
    google = object.__new__(GoogleSheetsRepository)
    monkeypatch.setattr(google, "_records", lambda sheet: records[sheet])
    target = InMemoryRepository()

    google.hydrate(target)

    assert target.payments == {}


def test_change_proposal_never_uses_informational_total_as_donor(monkeypatch):
    project = client.post("/api/projects", json={"project_code": "CZ.P", "project_name": "P", "recipient_name": "P"}).json()
    common = {"planned_future_spending": 0, "minimum_remaining_amount": 0,
              "transfer_locked": False, "donor_priority": 100, "is_leaf": True}
    rows = [
        {**common, "code": "3", "category": "informational", "total_amount": 1000000, "cumulative_spent": 0},
        {**common, "code": "1.1.1.1.2", "category": "direct", "total_amount": 100000, "cumulative_spent": 10000},
        {**common, "code": "1.1.1.1.3", "category": "direct", "total_amount": 100000, "cumulative_spent": 143477.20},
    ]
    import app.main as main
    monkeypatch.setattr(main, "budget_status", lambda *_args, **_kwargs: rows)

    proposal = client.post(f"/api/projects/{project['project_id']}/change-proposals/generate", json={}).json()

    assert proposal["transfers"]
    assert all(transfer["source_code"] != "3" for transfer in proposal["transfers"])
    assert proposal["transfers"][0]["source_code"] == "1.1.1.1.2"


def test_budget_change_rejects_even_one_haler_difference(monkeypatch):
    project = client.post("/api/projects", json={"project_code": "CZ.CH", "project_name": "Změna", "recipient_name": "P"}).json()
    with open("samples/Export_2026-07-11_084920.xlsx", "rb") as f:
        analyzed = client.post(f"/api/projects/{project['project_id']}/budgets/analyze", files={"file": ("budget.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    client.post(f"/api/projects/{project['project_id']}/budgets/import", json={"token": analyzed.json()["token"]})

    import app.main as main
    current = repo.budgets[project["project_id"]][0]["analysis"]
    changed = current.model_copy(update={"total_amount": current.total_amount + Decimal("0.01")})
    monkeypatch.setattr(main, "parse_budget", lambda _data, _name: changed)
    preview = client.post(f"/api/projects/{project['project_id']}/budget-change/analyze", files={"file": ("change.xlsx", b"test", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert preview.status_code == 200
    assert preview.json()["errors"]
    assert client.post(f"/api/projects/{project['project_id']}/budget-change/import", json={"token": preview.json()["token"]}).status_code == 422


def test_lump_sum_and_cofinancing_crud():
    project = client.post("/api/projects", json={"project_code": "CZ.S", "project_name": "Spoření", "recipient_name": "P"}).json()
    project_id = project["project_id"]

    lump = client.post(f"/api/projects/{project_id}/lump-sum-spending", json={
        "entry_date": "2026-07-11", "entry_mode": "cumulative", "monitoring_period": "aktuální", "entered_amount": 151000
    })
    assert lump.status_code == 201
    lump_id = lump.json()["lump_sum_entry_id"]
    assert client.patch(f"/api/projects/{project_id}/lump-sum-spending/{lump_id}", json={"entered_amount": 152000}).json()["entered_amount"] == "152000"

    first = client.post(f"/api/projects/{project_id}/cofinancing", json={"entry_date": "2026-07-01", "amount": 30000, "note": "Dárce A"})
    second = client.post(f"/api/projects/{project_id}/cofinancing", json={"entry_date": "2026-07-11", "amount": 20000})
    assert first.status_code == second.status_code == 201
    assert first.json()["note"] == "Dárce A"
    status = client.get(f"/api/projects/{project_id}/cofinancing").json()
    assert status["secured"] == 50000.0
    first_id = first.json()["cofinancing_entry_id"]
    assert client.patch(f"/api/projects/{project_id}/cofinancing/{first_id}", json={"amount": 35000}).status_code == 200
    assert client.delete(f"/api/projects/{project_id}/cofinancing/{first_id}").status_code == 204
    assert client.get(f"/api/projects/{project_id}/cofinancing").json()["secured"] == 20000.0
