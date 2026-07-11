# Import XLSX

Import je dvoufázový: `/analyze` vrátí hash, statistiky, varování a krátkodobý token;
`/import` data uloží až po potvrzení. Parser nejdřív zkusí openpyxl. Při chybě čte ZIP/XML,
workbook relace a typy `inlineStr`, `str`, `n`, `b` i prázdné buňky. Hierarchie vychází z
kódu, úrovně a nejbližšího existujícího rodiče. Export vytváří nový validní sešit a doplní
vzorce listů, koncových řádků a paušálu; originál se nemění.
