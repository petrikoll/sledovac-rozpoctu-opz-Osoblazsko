"""Single-writer request transactions for the Sheets-backed deployment.

Sheets batchUpdate is atomic, but has no cross-process compare-and-swap. Run one
application worker/instance per spreadsheet; use a transactional DB for scaling.
"""
import asyncio
import os
from copy import deepcopy
from contextvars import ContextVar
from weakref import WeakKeyDictionary

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

pending_writes: ContextVar[list | None] = ContextVar("pending_sheet_writes", default=None)


def stage(operation: str, *args) -> bool:
    pending = pending_writes.get()
    if pending is None:
        return False
    pending.append((operation, deepcopy(args)))
    return True


def install_transactions(app, repository, storage, caches):
    locks = WeakKeyDictionary()
    uncertain_remote = None

    @app.middleware("http")
    async def transaction(request, call_next):
        nonlocal uncertain_remote
        if not request.url.path.startswith("/api/") or request.url.path == "/api/health":
            return await call_next(request)
        lock = locks.setdefault(asyncio.get_running_loop(), asyncio.Lock())
        # Readers must not observe state before its persistent commit succeeds.
        async with lock:
            remote = storage()
            if remote is None and os.getenv("ENVIRONMENT") == "production":
                return JSONResponse({"detail": "Trvalé úložiště není nakonfigurováno. Operace byla zastavena, aby se data neztratila."}, status_code=503)
            if uncertain_remote is not remote:
                uncertain_remote = None
            if uncertain_remote is not None:
                try:
                    fresh = type(repository)()
                    await run_in_threadpool(remote.hydrate, fresh)
                    repository.__dict__.update(fresh.__dict__)
                    uncertain_remote = None
                except Exception:
                    return JSONResponse({"detail": "Úložiště není dostupné. Data nyní nelze bezpečně načíst ani uložit."}, status_code=503)
            write = request.method not in {"GET", "HEAD", "OPTIONS"}
            if not write:
                return await call_next(request)
            previous = deepcopy(repository.__dict__)
            previous_caches = [deepcopy(cache) for cache in caches]
            writes = []
            token = pending_writes.set(writes)

            def rollback():
                repository.__dict__.clear()
                repository.__dict__.update(previous)
                for cache, saved in zip(caches, previous_caches):
                    cache.clear()
                    cache.update(saved)

            try:
                response = await call_next(request)
                if response.status_code >= 400:
                    rollback()
                elif writes and remote:
                    try:
                        await run_in_threadpool(remote.commit, writes)
                    except Exception:
                        # Transport failures can have an unknown outcome. Never
                        # retry a write blindly; reconcile before the next read.
                        rollback()
                        uncertain_remote = remote
                        return JSONResponse({"detail": "Uložení se nepodařilo potvrdit. Obnovte data a zkontrolujte poslední stav; změny neopakujte naslepo."}, status_code=503)
                return response
            except Exception:
                rollback()
                raise
            finally:
                pending_writes.reset(token)
