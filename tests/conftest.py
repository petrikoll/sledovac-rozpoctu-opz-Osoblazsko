import sys
import os
from pathlib import Path

# Tests must never inherit credentials for production storage.
for key in ("GOOGLE_SERVICE_ACCOUNT_JSON", "GOOGLE_SPREADSHEET_ID", "GOOGLE_DRIVE_FOLDER_ID", "GOOGLE_CLIENT_ID"):
    os.environ.pop(key, None)
os.environ["ENVIRONMENT"] = "development"
sys.path.insert(0, str(Path(__file__).parents[1] / "backend"))

import pytest
from io import BytesIO
import openpyxl
from app.xlsx_parser import HEADERS


@pytest.fixture
def budget_xlsx():
    """Synthetic hierarchy: no payroll or recipient documents needed in CI."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Export"
    ws.append(HEADERS)
    for code, name, total, units, price, percent in [
        ("1", "Celkem", 4415040, None, None, None),
        ("1.1", "Osobní náklady", 3153600, None, None, None),
        ("1.1.1", "Pracovní smlouvy", 3153600, None, None, None),
        ("1.1.1.1", "Pozice A", 2232000, 24, 93000, None),
        ("1.1.1.2", "Pozice B", 921600, 24, 38400, None),
        ("1.2", "Paušál", 1261440, None, None, 40),
        ("2", "Nezpůsobilé", 0, None, None, None),
        ("3", "Informativní", 0, None, None, None),
        ("4", "Informativní 2", 0, None, None, None),
    ]:
        ws.append([code, name, "měsíc", price, units, total, None, len(code.split(".")), percent])
    output = BytesIO()
    wb.save(output)
    return output.getvalue()
