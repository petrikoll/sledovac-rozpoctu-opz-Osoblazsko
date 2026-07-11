# Implementační plán

1. Ověřit strukturu dodaného XLSX a textovou vrstvu všech tří PDF.
2. Vytvořit peněžní doménové modely založené na `Decimal` a čisté výpočtové služby.
3. Implementovat dvoustupňový XLSX parser, validaci hierarchie a export vzorců.
4. Implementovat PDF parser (PyMuPDF + pdfplumber), hlavičku, finanční souhrny a SD2.
5. Oddělit aplikační služby od `InMemoryRepository` a Google Sheets/Drive adaptérů.
6. Vytvořit dvoufázové importy a REST API ve FastAPI včetně autorizace a kontrol hashů.
7. Vytvořit české React UI pro projekty, dashboard, rozpočet, ŽoP, paušál a importy.
8. Přidat testy parserů, výpočtů, API a hlavních frontendových komponent.
9. Přidat Docker/Render konfiguraci, dokumentaci provozu a bezpečnosti.
10. Spustit testy nad skutečnými ukázkami a opravit zjištěné odchylky.

Importy jsou potvrzované ve dvou krocích. Originály se nemění; lokální běh používá
in-memory úložiště a produkce servisní účet pro Google Sheets a Drive.
