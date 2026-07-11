# Nasazení na Render

1. Pushněte repozitář do soukromého GitHub repozitáře.
2. V Renderu zvolte New Blueprint a `render.yaml`.
3. Jako Secret vyplňte všechny Google proměnné z `.env.example` a `ALLOWED_EMAILS`.
4. Nastavte OAuth Authorized JavaScript origin a redirect/origin na HTTPS doménu služby.
5. Nasazení ověřte přes `/api/health`, následně přihlášení povoleného i zakázaného účtu.
6. Proveďte kontrolní import kopií ukázkových souborů a zkontrolujte Sheet a Drive.

Free instance uspává proces; data proto nesmí být na lokálním disku. Tajné JSON hodnoty
nevypisujte do logu. Před ostrým provozem nastavte retenci Drive, zálohování Sheetu a
organizační proces rotace servisního klíče.
