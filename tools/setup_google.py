"""Jednorázová bezpečná inicializace Google úložiště bez výpisu tajných údajů."""
import argparse
import json
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

from app.repository import GoogleSheetsRepository


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", required=True)
    parser.add_argument("--sheet", required=True)
    parser.add_argument("--folder", required=True)
    args = parser.parse_args()

    key_text = Path(args.key).read_text(encoding="utf-8")
    info = json.loads(key_text)
    repository = GoogleSheetsRepository(args.sheet, key_text)
    repository.ensure_schema()

    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive"]
    )
    drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
    folder = drive.files().get(fileId=args.folder, fields="id,name,mimeType").execute()
    if folder.get("mimeType") != "application/vnd.google-apps.folder":
        raise RuntimeError("Zadané ID nepatří složce Google Drive.")
    print("OK: tabulka i složka jsou dostupné; schéma bylo inicializováno.")


if __name__ == "__main__":
    main()
