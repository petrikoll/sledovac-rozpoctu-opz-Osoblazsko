# Sledovač čerpání rozpočtu projektů OPZ+

Interní česká aplikace pro projekty financované ex-ante. Importuje skutečné exporty
rozpočtu XLSX (včetně poškozené varianty bez `sharedStrings.xml`) a textové PDF žádostí
o platbu, počítá schválené čerpání, paušální nárok, zůstatky, návrhy přesunů a závěrečné
vypořádání. Peněžní výpočty backendu používají `Decimal`.

## Architektura

- `frontend/`: React, TypeScript, Vite, TanStack Query, React Hook Form
- `backend/app/`: FastAPI, parsery, doména, Google adaptéry a in-memory vývojové úložiště
- `samples/`: nepovinné soukromé podklady pro lokální integrační testy (nepatří do Gitu)
- `tests/`: pytest nad syntetickými daty, výpočty, autorizací a API
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
npm ci
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

Produkce vyžaduje ověřený Google ID token a povolený přístup. API validuje příponu, MIME typ,
limit velikosti a SHA-256; duplicitní import odmítá. Do technických logů se nemá
zapisovat obsah dokumentů ani tokeny. Uživatelská historie obsahuje e-mail autora
a názvy zdrojových souborů, proto se zobrazuje pouze uživatelům daného projektu.
HTTPS zajišťuje Render.

## Opravy a nové ovládání (září 2026)

- Administrátor najde v horní navigaci **Přístupy**. Může povolit nebo zakázat účet,
  určit roli a vybrat jednotlivé projekty nebo příjemce. Pravidlo v listu `PRISTUPY`
  má přednost před starším `ALLOWED_EMAILS` a pevnými omezeními v kódu. Stávající
  uživatelé si zachovají dosavadní přístupy, dokud správce nevytvoří nové pravidlo.
  Google OAuth v režimu Testing nadále vyžaduje přidání testovacího uživatele v Google Cloud.
- V detailu SD2 je **Historie období a obnova**. Před uložením, smazáním nebo ZIP
  importem se zálohuje předchozí stav. Historie vzniká až od nasazení této funkce;
  neobnovuje starší smazaná data ani soubory na Google Disku. Obnova je rovněž zálohována.
- Souběžná úprava stejného období se odmítne chybou 409. Neuložené změny zůstávají
  ve formuláři; před jejich zahozením je možné stáhnout pracovní XML. Poté načtěte
  aktuální stav a změny porovnejte. Neexistuje automatické sloučení dvou úprav.
- Při vypršení přihlášení zůstává rozpracovaný formulář pouze v paměti otevřeného
  okna. Přihlášení stejným účtem jej obnoví; jiný účet vymaže formulář i klientskou
  mezipaměť. Obnovení nebo zavření stránky rozpracovaná data nezachová. Mzdová data
  se kvůli této funkci neukládají do localStorage.
- Výpočet SD2 používá projektový podíl mzdy podle fondu a hodin, korekce a odvody.
  U původních ručně zadaných řádků bez fondu zůstává částka interpretována jako
  projektová. U řádků je vidět rozpad výpočtu a zdrojový soubor. Export prázdného
  XML je nadále možný s upozorněním.
- Nejvyšší verze každé ŽoP je jediná započtená do výpočtů; starší verze zůstávají
  uložené. Dosud neschválené výdaje se nevykazují jako schválené.
- ZIP import ignoruje obsahově totožné PDF a odmítá nejednoznačné podklady pro
  stejného pracovníka, vztah, položku a měsíc. Přímý import zachovává kolegy na
  stejné položce, jiné měsíce i stabilní XML ID. U více M01 v Mostech je potřeba
  ověřit a výslovně vybrat projektovou složku; pouhé pořadí částek nestačí.

### Bezpečné ukládání do Sheets

Změny jednoho požadavku včetně historie se odešlou jako jediný atomický
`spreadsheets.batchUpdate`. Při chybě se vrátí paměťový stav; po neurčitém výsledku
síťové operace aplikace nejprve znovu načte úložiště. Zápis se neopakuje automaticky.
V produkci bez nakonfigurovaného trvalého úložiště aplikace vrací 503.

**Pro jednu tabulku smí zapisovat pouze jedna instance / jeden worker aplikace.**
Sheets nemají databázový compare-and-swap napříč procesy. Provoz více instancí,
současný lokální backend nad produkční tabulkou nebo ruční úpravy tabulky během
provozu nejsou bezpečné. Pro takové nasazení je potřeba transakční databáze.
Historie uvnitř stejné tabulky nenahrazuje oddělenou pravidelnou zálohu celé tabulky.

Před nasazením vytvořte zálohu Sheets. První start přidá listy `PRISTUPY` a
`SD2_HISTORIE` a doplní zdrojové sloupce v `SD2_MESICE`; stávající záznamy nemaže.
Nasazujte frontend a backend společně a poté aplikaci obnovte. Starý otevřený
klient bez kontroly verze nesmí zapisovat SD2 a obdrží výzvu k obnovení.

### Automatické kontroly

GitHub Actions spouštějí backend testy, frontend testy, produkční sestavení a
kontrolu produkčních npm závislostí. Soukromé PDF/XLSX se do CI neposílají; jediný
nepovinný test skutečných PDF se bez lokálních vzorů přeskočí. Testovací konfigurace
odstraňuje zděděné Google přihlašovací údaje, aby testy nemohly zapisovat do produkce.

## Známé hranice

PDF bez textové vrstvy vyžaduje externí OCR. SD2 parser ukládá zdrojovou stranu a používá
tabulkovou/koordinační extrakci; jiné verze sestavy MS2021+ je nutné před ostrým importem
zkontrolovat v náhledu. Google adaptéry vyžadují skutečné přihlašovací údaje a nejsou v
lokálních testech volány.
