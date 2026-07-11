# Datový model

Google tabulka obsahuje listy `USERS`, `PROJEKTY`, `VERZE_ROZPOCTU`, `POLOZKY_ROZPOCTU`,
`ZADOSTI_O_PLATBU`, `RADKY_ZOP`, `PLATBY_A_ZALOHY`, `UTRATA_PAUSALU`, `NAVRHY_ZMEN`,
`NAVRHY_ZMEN_RADKY` a `IMPORT_LOG`. Přesná záhlaví jsou jediným zdrojem pravdy v
`backend/app/repository.py`. Verze rozpočtu a revize ŽoP jsou neměnné. Aktivní stav je
odkaz, historie se nemaže. Peníze se zapisují jako čísla bez měnové přípony.
