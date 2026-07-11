import argparse
import json
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", required=True)
    parser.add_argument("--sheet", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--role", choices=["admin", "editor", "user"], required=True)
    args = parser.parse_args()
    info = json.loads(Path(args.key).read_text(encoding="utf-8"))
    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    values = build("sheets", "v4", credentials=credentials, cache_discovery=False).spreadsheets().values()
    rows = values.get(spreadsheetId=args.sheet, range="USERS!A2:C").execute().get("values", [])
    for index, row in enumerate(rows, start=2):
        if row and row[0].strip().lower() == args.email.lower():
            values.update(spreadsheetId=args.sheet, range=f"USERS!B{index}", valueInputOption="RAW",
                          body={"values": [[args.role]]}).execute()
            print(f"OK: {args.email} má roli {args.role}.")
            return
    raise RuntimeError("Uživatel nebyl v listu USERS nalezen.")


if __name__ == "__main__":
    main()
