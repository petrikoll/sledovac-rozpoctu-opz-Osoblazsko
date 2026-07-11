# Sledovač čerpání rozpočtu projektů OPZ+

Interní česká aplikace pro projekty financované ex-ante. Importuje skutečné exporty
rozpočtu XLSX (včetně poškozené varianty bez `sharedStrings.xml`) a textové PDF žádostí
o platbu, počítá schválené čerpání, paušální nárok, zůstatky, návrhy přesunů a závěrečné
vypořádání. Peněžní výpočty backendu používají `Decimal`.

## Architektura

- `frontend/`: React, TypeScript, Vite, TanStack Query, React Hook Form
- `backend/app/`: FastAPI, parsery, doména, Google adaptéry a in-memory vývojové úložiště
- `samples/`: původní dodané soubory pro integrační testy
- `tests/`: pytest nad skutečnými soubory a API
- produkce: jediná Docker web service na Renderu; originály v Google Drive, data v Sheets

## Lokální spuštění

Vyžaduje Python 3.12 a Node 22+.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
$env:PYTHONPATH="backend"
uvicorn app.main:app --reload
```

V druhém terminálu:

```powershell
cd frontend
npm install
npm run dev
```

V režimu `development` bez `GOOGLE_CLIENT_ID` backend dovolí lokálního administrátora a
použije paměťové úložiště. Tento režim nikdy nepoužívejte veřejně.

Testy: `pytest -q`; frontend: `cd frontend`, `npm test`, `npm run build`.

## Google Cloud

1. Založte Google Cloud projekt, zapněte Google Sheets API a Google Drive API.
2. Vytvořte servisní účet a JSON klíč. Celý JSON vložte jako jedinou hodnotu
   `GOOGLE_SERVICE_ACCOUNT_JSON` (nikdy jej necommitujte).
3. Vytvořte Sheet a Drive složku a obojí sdílejte s e-mailem servisního účtu jako editor.
4. ID tabulky a složky nastavte do `GOOGLE_SPREADSHEET_ID` a `GOOGLE_DRIVE_FOLDER_ID`.
5. V OAuth consent screen vytvořte Web client, nastavte doménu Renderu a jeho Client ID
   vložte do `GOOGLE_CLIENT_ID`. Povolené adresy oddělte čárkami v `ALLOWED_EMAILS`.

`GoogleSheetsRepository.ensure_schema()` vytvoří chybějící listy a hlavičky dávkově.
Pravidelně zálohujte Sheet přes Soubor → Stáhnout → Microsoft Excel a Drive pravidly
organizace. Omezte servisní účet na jedinou složku a tabulku, rotujte klíče a auditujte
seznam povolených uživatelů.

## Render

Připojte repozitář jako Blueprint podle `render.yaml`, vyplňte všechny tajné proměnné a
nasazení spusťte. Health check je `/api/health`. Kontejner nepočítá s trvalým lokálním
diskem. Detailní checklist je v `docs/DEPLOYMENT_RENDER.md`.

## Bezpečnost

Produkce vyžaduje ověřený Google ID token a allowlist. API validuje příponu, MIME typ,
limit 20 MB a SHA-256; duplicitní import odmítá. Do logů se nezapisuje obsah dokumentů,
tokeny ani bankovní/osobní údaje. HTTPS zajišťuje Render.

## Známé hranice

PDF bez textové vrstvy vyžaduje externí OCR. SD2 parser ukládá zdrojovou stranu a používá
tabulkovou/koordinační extrakci; jiné verze sestavy MS2021+ je nutné před ostrým importem
zkontrolovat v náhledu. Google adaptéry vyžadují skutečné přihlašovací údaje a nejsou v
lokálních testech volány.
