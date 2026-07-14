from fastapi.testclient import TestClient
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from app.main import app, repo
from app.models import PaymentRequest, Sd2MonthlyEntry

client = TestClient(app)


def _payment(sequence: int, *, state: str, processing_state: str, advance: bool = False,
             final: bool = False, declared: Decimal = Decimal("0"), approved: Decimal = Decimal("0"),
             public_payment: Decimal = Decimal("0")) -> PaymentRequest:
    return PaymentRequest(
        project_code="CZ.SETTLEMENT", project_name="Vypořádání", recipient_name="Příjemce",
        sequence_number=sequence, request_number=f"CZ.SETTLEMENT/{sequence}", request_version=1,
        request_type="ANTE", state=state, processing_state=processing_state,
        is_final_payment=final, is_advance_payment=advance,
        declared_direct_costs=declared, approved_direct_costs=approved,
        declared_lump_sum=0, approved_lump_sum=0, public_payment=public_payment,
        approved_total=approved, source_sha256=f"hash-{sequence}", source_file_name=f"zop-{sequence}.pdf",
    )


def test_final_settlement_separates_paid_advance_from_pending_final_claim():
    project = client.post("/api/projects", json={
        "project_code": "CZ.SETTLEMENT", "project_name": "Vypořádání", "recipient_name": "Příjemce",
        "public_funding_rate": 1,
    }).json()
    project_id = project["project_id"]
    repo.payments[project_id] = [
        _payment(1, state="Proplacená", processing_state="Proplacená příjemci/Vypořádaná",
                 advance=True, public_payment=Decimal("300")),
        _payment(2, state="Proplacená", processing_state="Proplacená příjemci/Vypořádaná",
                 declared=Decimal("100"), approved=Decimal("100"), public_payment=Decimal("100")),
        _payment(3, state="Zaregistrovaná", processing_state="Zaregistrovaná", final=True,
                 declared=Decimal("50"), approved=Decimal("50"), public_payment=Decimal("0")),
    ]

    response = client.get(f"/api/projects/{project_id}/final-settlement")

    assert response.status_code == 200
    data = response.json()
    assert Decimal(data["initial_advance"]) == Decimal("300")
    assert Decimal(data["net_received"]) == Decimal("400")
    assert Decimal(data["approved_eligible_total"]) == Decimal("100.00")
    assert Decimal(data["submitted_pending_total"]) == Decimal("50.00")
    assert Decimal(data["settlement"]) == Decimal("-250.00")
    assert Decimal(str(data["rows"][2]["approved_total"])) == Decimal("0")
    assert Decimal(str(data["rows"][2]["projected_for_settlement"])) == Decimal("50")

    exported = client.get(f"/api/projects/{project_id}/final-settlement.xlsx")
    assert exported.status_code == 200
    assert exported.content.startswith(b"PK")


def test_final_settlement_prefers_actual_coverage_from_financial_plan():
    project = client.post("/api/projects", json={
        "project_code": "CZ.FINPLAN", "project_name": "Finanční plán", "recipient_name": "Příjemce",
        "public_funding_rate": 1,
    }).json()
    payment = _payment(1, state="Proplacená", processing_state="Proplacená příjemci/Vypořádaná",
                       advance=True, public_payment=Decimal("1100"))
    payment.financial_plan_coverage_actual = Decimal("1000")
    payment.financial_plan_settlement_actual = Decimal("0")
    repo.payments[project["project_id"]] = [payment]

    data = client.get(f"/api/projects/{project['project_id']}/final-settlement").json()

    assert Decimal(data["net_received"]) == Decimal("1000")
    assert Decimal(data["initial_advance"]) == Decimal("1000")
    assert Decimal(str(data["rows"][0]["pdf_public_payment"])) == Decimal("1100")


def test_financial_plan_coverage_deducts_recipient_cofinancing():
    project = client.post("/api/projects", json={
        "project_code": "CZ.COFIN", "project_name": "Spolufinancování", "recipient_name": "Příjemce",
        "public_funding_rate": "0.95",
    }).json()
    payment = _payment(1, state="Proplacená", processing_state="Proplacená příjemci/Vypořádaná",
                       advance=True, public_payment=Decimal("1258286.40"))
    payment.financial_plan_coverage_actual = Decimal("1324512.00")
    repo.payments[project["project_id"]] = [payment]

    data = client.get(f"/api/projects/{project['project_id']}/final-settlement").json()
    listed = client.get(f"/api/projects/{project['project_id']}/payment-requests").json()

    assert Decimal(str(data["net_received"])) == Decimal("1258286.40")
    assert Decimal(str(listed[0]["financial_plan_provider_payment"])) == Decimal("1258286.40")


def test_pdf_paid_amount_wins_over_one_cent_rounding_difference():
    project = client.post("/api/projects", json={
        "project_code": "CZ.ROUND", "project_name": "Zaokrouhlení", "recipient_name": "Příjemce",
        "public_funding_rate": "0.95",
    }).json()
    payment = _payment(2, state="Proplacená", processing_state="Proplacená příjemci/Vypořádaná",
                       public_payment=Decimal("578689.64"))
    payment.financial_plan_coverage_actual = Decimal("609147.00")
    payment.financial_plan_state = "Proplacená příjemci/Vypořádaná"
    repo.payments[project["project_id"]] = [payment]

    data = client.get(f"/api/projects/{project['project_id']}/final-settlement").json()

    assert Decimal(str(data["net_received"])) == Decimal("578689.64")


def test_planned_financial_plan_row_is_not_counted_as_received():
    project = client.post("/api/projects", json={
        "project_code": "CZ.PLANNED", "project_name": "Plán", "recipient_name": "Příjemce",
        "public_funding_rate": "0.95",
    }).json()
    payment = _payment(7, state="Zaregistrovaná", processing_state="Zaregistrovaná", final=True)
    payment.financial_plan_coverage_actual = Decimal("0.01")
    payment.financial_plan_state = "Plánovaná"
    repo.payments[project["project_id"]] = [payment]

    data = client.get(f"/api/projects/{project['project_id']}/final-settlement").json()

    assert Decimal(str(data["net_received"])) == Decimal("0.00")


def test_mosty_malikova_bonus_is_offered_but_excluded_by_default():
    from app.main import _mosty_payroll_rows

    base = {
        "source_key": "malikova", "first_name": "Silvie", "last_name": "Malíková",
        "month": "2026-05-01", "contract_name": "PS", "contract_gross": Decimal("77143"),
        "work_time_fund": Decimal("134.4"), "full_time_fund": Decimal("168"),
        "total_fte": Decimal("0.8"), "vacation_hours": Decimal("16"), "vacation_days": Decimal("2.5"),
        "employment_type": "Smlouva", "component_name": "Základní mzda",
    }
    rows = [
        {**base, "source_key": "m1", "component_code": "M01", "component_occurrence": 1, "component_amount": Decimal("51000")},
        {**base, "source_key": "m2", "component_code": "M01", "component_occurrence": 2, "component_amount": Decimal("5381")},
        {**base, "source_key": "m3", "component_code": "M01", "component_occurrence": 3, "component_amount": Decimal("10762")},
        {**base, "source_key": "b1", "component_code": "O01", "component_occurrence": 1, "component_name": "Prémie pevnou částkou", "component_amount": Decimal("10000")},
    ]

    result = _mosty_payroll_rows(rows, {"1.1.1.3"})

    assert len(result) == 1
    assert result[0]["component_amount"] == Decimal("10762")
    assert result[0]["other_with_contributions"] == Decimal("-8523.75")
    assert result[0]["project_bonus_available"] == Decimal("10000")
    assert result[0]["project_vacation_hours"] == Decimal("4.00")


def test_srssjesenik_can_only_view_jesenicko_project():
    jesenicko = client.post("/api/projects", json={
        "project_code": "CZ.03.02.01/00/24_065/0004961",
        "project_name": "Jesenicko proti dluhům III",
        "recipient_name": "P",
    }).json()
    other = client.post("/api/projects", json={
        "project_code": "CZ.OTHER",
        "project_name": "Jiný projekt",
        "recipient_name": "P",
    }).json()
    from app.main import can_view_project
    user = {"email": "srssjesenik@gmail.com", "role": "editor"}

    assert can_view_project(jesenicko["project_id"], user) is True
    assert can_view_project(other["project_id"], user) is False


def test_editor_can_save_worker_assignments():
    project = client.post("/api/projects", json={
        "project_code": "CZ.MOSTY.WORKERS",
        "project_name": "Mosty v rodině",
        "recipient_name": "P",
    }).json()
    from app.main import require_editor
    app.dependency_overrides[require_editor] = lambda: {
        "email": "ucetni@example.cz",
        "role": "editor",
    }
    try:
        response = client.put(f"/api/projects/{project['project_id']}/worker-assignments", json={
            "assignments": [{
                "budget_item_code": "1.1.1.1",
                "employee_names": "Jana Nováková, Petr Novák",
            }],
        })
    finally:
        app.dependency_overrides.pop(require_editor, None)

    assert response.status_code == 200
    assert response.json()[0]["employee_names"] == "Jana Nováková, Petr Novák"
    saved = client.get(f"/api/projects/{project['project_id']}/worker-assignments")
    assert saved.status_code == 200
    assert saved.json()[0]["budget_item_code"] == "1.1.1.1"


def test_delete_sd2_period_keeps_other_periods():
    project = client.post("/api/projects", json={"project_code": "CZ.MOSTY", "project_name": "Mosty v rodině", "recipient_name": "P"}).json()
    project_id = project["project_id"]
    repo.sd2_entries[project_id] = [
        Sd2MonthlyEntry(monitoring_period=1, month=date(2023, 11, 1), budget_item_code="1.1.1.1", gross_wage=100),
        Sd2MonthlyEntry(monitoring_period=2, month=date(2024, 1, 1), budget_item_code="1.1.1.1", gross_wage=200),
    ]
    repo.sd2_attachments[project_id] = [
        {"attachment_id": "a1", "monitoring_period": 1},
        {"attachment_id": "a2", "monitoring_period": 2},
    ]

    response = client.delete(f"/api/projects/{project_id}/sd2-period?period=1")

    assert response.status_code == 204
    assert [entry.monitoring_period for entry in repo.sd2_entries[project_id]] == [2]
    assert [entry["monitoring_period"] for entry in repo.sd2_attachments[project_id]] == [2]


def test_sd2_xml_download_does_not_save_entries(monkeypatch):
    project = client.post("/api/projects", json={"project_code": "CZ.XML", "project_name": "XML", "recipient_name": "P"}).json()
    project_id = project["project_id"]
    import app.main as main
    monkeypatch.setattr(main, "sd2_budget_items", lambda _project_id: [SimpleNamespace(code="1.1.1.1")])
    payload = {"entries": [{
        "monitoring_period": 5, "month": "2026-07-01", "budget_item_code": "1.1.1.1",
        "gross_wage": 1000, "employer_contributions": 338, "other_with_contributions": 0,
        "other_without_contributions": 0, "payment_date": "2026-08-10", "subject_id": "12345678",
        "last_name": "Nováková", "first_name": "Jana", "employment_type": "Smlouva",
        "work_time_fund": 168, "project_hours": 80, "description": "",
    }]}

    response = client.post(f"/api/projects/{project_id}/sd2-xml?period=5", json=payload)

    assert response.status_code == 200
    assert b"<ns2:TYPDOKLADU>Mzdy</ns2:TYPDOKLADU>" in response.content
    assert repo.sd2_entries[project_id] == []


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
        "UTRATA_PAUSALU": [], "SPOLUFINANCOVANI": [], "SD2_MESICE": [], "SD2_PRILOHY": [],
        "HARMONOGRAM_PROJEKTU": [], "IMPORT_LOG": [],
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
        # Simuluje i stará data, kde byl informační kód 3 chybně uložen jako direct.
        {**common, "code": "3", "category": "direct", "total_amount": 1000000, "cumulative_spent": 0},
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
