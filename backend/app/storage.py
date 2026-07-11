import json
from io import BytesIO
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload


class LocalFileStorage:
    def __init__(self, root: str): self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)
    def upload(self, name: str, data: bytes, mime: str) -> str:
        path = self.root / name
        path.write_bytes(data)
        return str(path)


class GoogleDriveStorage:
    def __init__(self, folder_id: str, service_account_json: str):
        creds = service_account.Credentials.from_service_account_info(json.loads(service_account_json), scopes=["https://www.googleapis.com/auth/drive"])
        self.api = build("drive", "v3", credentials=creds, cache_discovery=False).files()
        self.folder_id = folder_id

    def upload(self, name: str, data: bytes, mime: str) -> str:
        media = MediaIoBaseUpload(BytesIO(data), mimetype=mime, resumable=False)
        return self.api.create(body={"name": name, "parents": [self.folder_id]}, media_body=media, fields="id").execute()["id"]
