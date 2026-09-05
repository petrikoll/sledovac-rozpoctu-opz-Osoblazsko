import hashlib
import json
from datetime import datetime
from uuid import uuid4


def revision(entries) -> str:
    data = sorted((entry.model_dump(mode="json") for entry in entries), key=lambda e: e["sd2_entry_id"])
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def snapshot_rows(project_id, period, entries, user, action):
    payload = json.dumps([entry.model_dump(mode="json") for entry in entries], ensure_ascii=False)
    meta = {"snapshot_id": str(uuid4()), "project_id": project_id, "monitoring_period": period,
            "created_at": datetime.utcnow().isoformat(), "created_by": user["email"], "action": action}
    # Sheets limits individual cells. Chunk the snapshot, including empty state.
    return [{**meta, "chunk_index": index // 8000, "payload": payload[index:index + 8000]}
            for index in range(0, len(payload), 8000)]


def snapshots(rows, project_id, period):
    grouped = {}
    for row in rows:
        if row.get("project_id") == project_id and int(row.get("monitoring_period", 0)) == period:
            grouped.setdefault(row["snapshot_id"], []).append(row)
    result = []
    for chunks in grouped.values():
        chunks.sort(key=lambda row: int(row["chunk_index"]))
        entries = json.loads("".join(row["payload"] for row in chunks))
        result.append({**{k: v for k, v in chunks[0].items() if k not in {"payload", "chunk_index"}}, "entries": entries, "entry_count": len(entries)})
    return sorted(result, key=lambda row: row["created_at"], reverse=True)
