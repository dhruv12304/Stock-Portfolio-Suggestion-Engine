"""Schema-versioned, atomic persistence for named portfolio snapshots.

Replaces the single anonymous ``data/saved_portfolio.json`` model with a
directory of named snapshots tracked by ``data/portfolios/index.json``.

Design contract:

- Every disk write is atomic (tempfile + ``os.replace``).
- Parent directories are created on demand; first-build on a fresh clone works.
- Validation failures move the offending file to ``<id>.quarantine-<ts>.json``
  instead of deleting. User data is never destroyed.
- Every JSON document carries a ``schema_version`` so migrations can be
  layered without ambiguity.
- The module is pure I/O over JSON + filesystem. No yfinance, no Streamlit.

Directory layout::

    data/
      portfolios/
        index.json
        <uuid>.json
        <uuid>.quarantine-2026-05-16T1742.json
      value_history/
        <uuid>.csv
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import uuid
import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PORTFOLIOS_DIR = DATA_DIR / "portfolios"
VALUE_HISTORY_DIR = DATA_DIR / "value_history"
INDEX_FILE = PORTFOLIOS_DIR / "index.json"

REQUIRED_SNAPSHOT_KEYS = frozenset(
    {
        "schema_version",
        "id",
        "name",
        "built_at",
        "investment_amount",
        "strategies",
        "holdings",
    }
)
REQUIRED_INDEX_KEYS = frozenset({"schema_version", "items"})


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SnapshotMeta:
    """Lightweight index entry; everything needed to list portfolios."""

    id: str
    name: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SnapshotMeta":
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
        )


@dataclass
class Index:
    """In-memory representation of ``index.json``."""

    items: list[SnapshotMeta] = field(default_factory=list)
    active_id: str | None = None
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "active_id": self.active_id,
            "items": [m.to_dict() for m in self.items],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Index":
        return cls(
            items=[SnapshotMeta.from_dict(m) for m in data.get("items", [])],
            active_id=data.get("active_id"),
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
        )


class StorageError(Exception):
    """Raised when a snapshot cannot be persisted or retrieved cleanly."""


class SnapshotNotFoundError(StorageError):
    """A snapshot ID was referenced but does not exist on disk."""


# ---------------------------------------------------------------------------
# Path resolution (overridable for tests)
# ---------------------------------------------------------------------------
_active_data_dir: Path = DATA_DIR


def set_data_dir(path: Path) -> None:
    """Override the on-disk root used by storage operations.

    Tests use this to point the module at a ``tmp_path`` directory so they
    never touch the repo's real ``data/`` folder.
    """

    global _active_data_dir
    _active_data_dir = Path(path)


def get_data_dir() -> Path:
    return _active_data_dir


def portfolios_dir() -> Path:
    return get_data_dir() / "portfolios"


def value_history_dir() -> Path:
    return get_data_dir() / "value_history"


def index_path() -> Path:
    return portfolios_dir() / "index.json"


def snapshot_path(snapshot_id: str) -> Path:
    return portfolios_dir() / f"{snapshot_id}.json"


def value_history_path(snapshot_id: str) -> Path:
    return value_history_dir() / f"{snapshot_id}.csv"


def ensure_dirs() -> None:
    """Create the storage directories if they do not already exist."""

    portfolios_dir().mkdir(parents=True, exist_ok=True)
    value_history_dir().mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Atomic primitives
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_write_json(path: Path, data: Any) -> None:
    """Write ``data`` as JSON to ``path`` atomically.

    The write goes to a sibling tempfile inside the same directory (so
    ``os.replace`` is guaranteed to be an atomic rename on POSIX), and the
    parent directory is created on demand.
    """

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def atomic_read_json(path: Path) -> Any | None:
    """Read JSON from ``path``. Returns ``None`` if missing or unreadable."""

    path = Path(path)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("could not read JSON %s: %s", path, exc)
        return None


def read_value_history(snapshot_id: str) -> list[dict[str, Any]]:
    """Read ``value_history/<snapshot_id>.csv`` as normalized rows."""

    path = value_history_path(snapshot_id)
    if not path.exists():
        return []

    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                date_value = str(row.get("date", "")).strip()
                raw_value = row.get("value")
                if not date_value or raw_value in (None, ""):
                    continue
                try:
                    value = float(raw_value)
                except (TypeError, ValueError):
                    continue
                normalized: dict[str, Any] = {"date": date_value, "value": value}
                as_of = str(row.get("as_of", "")).strip()
                if as_of:
                    normalized["as_of"] = as_of
                rows.append(normalized)
    except OSError as exc:
        logger.warning("could not read value history %s: %s", path, exc)
        return []

    rows.sort(key=lambda row: str(row["date"]))
    return rows


def upsert_value_history(
    snapshot_id: str,
    *,
    date: str,
    value: float,
    as_of: str | None = None,
) -> None:
    """Insert or replace one recorded value row per portfolio per day."""

    ensure_dirs()
    rows = read_value_history(snapshot_id)
    updated = False
    for row in rows:
        if row.get("date") == date:
            row["value"] = float(value)
            if as_of:
                row["as_of"] = as_of
            updated = True
            break

    if not updated:
        next_row: dict[str, Any] = {"date": date, "value": float(value)}
        if as_of:
            next_row["as_of"] = as_of
        rows.append(next_row)

    rows.sort(key=lambda row: str(row["date"]))
    _atomic_write_value_history(value_history_path(snapshot_id), rows)


def _atomic_write_value_history(path: Path, rows: list[dict[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["date", "value", "as_of"])
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        "date": row.get("date", ""),
                        "value": row.get("value", ""),
                        "as_of": row.get("as_of", ""),
                    }
                )
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def quarantine(path: Path, reason: str = "validation_failed") -> Path | None:
    """Move ``path`` aside instead of deleting.

    The file is renamed to ``<stem>.quarantine-<UTC-ISO>.<ext>`` so a human
    can recover from it. Returns the new path, or ``None`` if the source did
    not exist.
    """

    path = Path(path)
    if not path.exists():
        return None

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = path.with_name(f"{path.stem}.quarantine-{stamp}{path.suffix}")
    try:
        shutil.move(str(path), str(target))
        logger.warning("quarantined %s -> %s (%s)", path, target, reason)
        return target
    except OSError as exc:
        logger.error("could not quarantine %s: %s", path, exc)
        return None


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------
def _validate_index_blob(blob: Any) -> bool:
    if not isinstance(blob, dict):
        return False
    if not REQUIRED_INDEX_KEYS.issubset(blob.keys()):
        return False
    if not isinstance(blob["items"], list):
        return False
    return True


def read_index() -> Index:
    """Read the on-disk index, quarantining the file if it is malformed."""

    ensure_dirs()
    path = index_path()
    if not path.exists():
        return Index()
    blob = atomic_read_json(path)
    if blob is None:
        # File is present but unreadable (truncated, invalid JSON, etc.).
        # Quarantining preserves the bytes for forensic recovery.
        quarantine(path, reason="unparseable_index")
        return Index()
    if not _validate_index_blob(blob):
        quarantine(path, reason="invalid_index_schema")
        return Index()
    try:
        return Index.from_dict(blob)
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("index parse failed (%s); quarantining", exc)
        quarantine(path, reason="invalid_index_payload")
        return Index()


def write_index(index: Index) -> None:
    atomic_write_json(index_path(), index.to_dict())


def set_active(snapshot_id: str | None) -> None:
    """Mark a snapshot as the active working portfolio (or clear)."""

    index = read_index()
    if snapshot_id is not None and not any(m.id == snapshot_id for m in index.items):
        raise SnapshotNotFoundError(snapshot_id)
    index.active_id = snapshot_id
    write_index(index)


def get_active() -> str | None:
    return read_index().active_id


def list_snapshots() -> list[SnapshotMeta]:
    return list(read_index().items)


# ---------------------------------------------------------------------------
# Snapshot CRUD
# ---------------------------------------------------------------------------
def _validate_snapshot_blob(blob: Any) -> bool:
    if not isinstance(blob, dict):
        return False
    if not REQUIRED_SNAPSHOT_KEYS.issubset(blob.keys()):
        return False
    if not isinstance(blob["holdings"], list):
        return False
    if not isinstance(blob["strategies"], list):
        return False
    return True


def _make_id() -> str:
    return uuid.uuid4().hex


def create_snapshot(name: str, payload: dict[str, Any]) -> SnapshotMeta:
    """Create a new named snapshot from ``payload`` and register it in the index.

    ``payload`` is the holdings/strategies blob produced by ``build_basket``.
    The function injects ``schema_version``, ``id``, ``name``, and timestamps.
    """

    ensure_dirs()
    snapshot_id = _make_id()
    now = _now_iso()
    snapshot = {
        **payload,
        "schema_version": SCHEMA_VERSION,
        "id": snapshot_id,
        "name": name,
        "built_at": payload.get("built_at") or now,
        "created_at": now,
        "updated_at": now,
    }
    if not _validate_snapshot_blob(snapshot):
        raise StorageError("snapshot payload failed validation")

    atomic_write_json(snapshot_path(snapshot_id), snapshot)

    meta = SnapshotMeta(
        id=snapshot_id,
        name=name,
        created_at=now,
        updated_at=now,
    )
    index = read_index()
    index.items.append(meta)
    if index.active_id is None:
        index.active_id = snapshot_id
    write_index(index)
    return meta


def read_snapshot(snapshot_id: str) -> dict[str, Any]:
    """Load a snapshot by ID. Quarantines and raises if the payload is invalid."""

    path = snapshot_path(snapshot_id)
    if not path.exists():
        raise SnapshotNotFoundError(snapshot_id)
    blob = atomic_read_json(path)
    if blob is None:
        # Present but unreadable -> quarantine + drop from index.
        quarantine(path, reason="unparseable_snapshot")
        _drop_from_index(snapshot_id)
        raise StorageError(f"snapshot {snapshot_id} was unreadable; quarantined")
    if not _validate_snapshot_blob(blob):
        quarantine(path, reason="invalid_snapshot_schema")
        _drop_from_index(snapshot_id)
        raise StorageError(f"snapshot {snapshot_id} failed validation; quarantined")
    return blob


def update_snapshot(snapshot_id: str, payload: dict[str, Any]) -> SnapshotMeta:
    """Replace the body of an existing snapshot, preserving id/name/timestamps."""

    existing = read_snapshot(snapshot_id)
    now = _now_iso()
    updated = {
        **existing,
        **payload,
        "schema_version": SCHEMA_VERSION,
        "id": snapshot_id,
        "name": existing["name"],
        "created_at": existing.get("created_at", now),
        "updated_at": now,
    }
    if not _validate_snapshot_blob(updated):
        raise StorageError("updated snapshot failed validation")
    atomic_write_json(snapshot_path(snapshot_id), updated)

    index = read_index()
    for i, meta in enumerate(index.items):
        if meta.id == snapshot_id:
            index.items[i] = SnapshotMeta(
                id=meta.id,
                name=meta.name,
                created_at=meta.created_at,
                updated_at=now,
            )
            break
    write_index(index)
    return next(m for m in index.items if m.id == snapshot_id)


def rename_snapshot(snapshot_id: str, new_name: str) -> SnapshotMeta:
    existing = read_snapshot(snapshot_id)
    now = _now_iso()
    existing["name"] = new_name
    existing["updated_at"] = now
    atomic_write_json(snapshot_path(snapshot_id), existing)

    index = read_index()
    for i, meta in enumerate(index.items):
        if meta.id == snapshot_id:
            index.items[i] = SnapshotMeta(
                id=meta.id,
                name=new_name,
                created_at=meta.created_at,
                updated_at=now,
            )
            write_index(index)
            return index.items[i]
    raise SnapshotNotFoundError(snapshot_id)


def duplicate_snapshot(snapshot_id: str, new_name: str | None = None) -> SnapshotMeta:
    """Clone a snapshot under a new ID. Does not change the active selection."""

    existing = read_snapshot(snapshot_id)
    cloned_name = new_name or f"{existing['name']} (copy)"
    payload = {
        k: v
        for k, v in existing.items()
        if k not in {"id", "name", "created_at", "updated_at"}
    }
    return create_snapshot(cloned_name, payload)


def delete_snapshot(snapshot_id: str) -> None:
    """Remove a snapshot from disk and the index."""

    path = snapshot_path(snapshot_id)
    if path.exists():
        path.unlink()

    _drop_from_index(snapshot_id)

    history = value_history_path(snapshot_id)
    if history.exists():
        history.unlink()


def _drop_from_index(snapshot_id: str) -> None:
    index = read_index()
    before = len(index.items)
    index.items = [m for m in index.items if m.id != snapshot_id]
    if index.active_id == snapshot_id:
        index.active_id = index.items[0].id if index.items else None
    if before != len(index.items) or (index.active_id is None and not index.items):
        write_index(index)


# ---------------------------------------------------------------------------
# Legacy migration
# ---------------------------------------------------------------------------
def migrate_legacy_if_needed(legacy_path: Path | None = None) -> SnapshotMeta | None:
    """If ``data/saved_portfolio.json`` exists, import it as the first snapshot.

    Two side effects beyond the JSON import:

    1. Migrates rows from the legacy ``portfolio_history.csv`` (keyed by a
       composition signature) into ``value_history/<id>.csv`` if any rows
       match the imported composition's signature.
    2. Archives both legacy files with ``.legacy.json`` / ``.legacy.csv``
       suffixes so a human can recover them; nothing is deleted.

    Returns the new snapshot meta, or ``None`` if there was nothing to
    migrate.
    """

    legacy_path = Path(legacy_path) if legacy_path else (get_data_dir() / "saved_portfolio.json")
    if not legacy_path.exists():
        return None

    blob = atomic_read_json(legacy_path)
    if not isinstance(blob, dict):
        quarantine(legacy_path, reason="legacy_unreadable")
        return None

    built_at = blob.get("built_at") or _now_iso()
    name = f"Imported {built_at}"
    payload = {
        "schema_version": SCHEMA_VERSION,
        "id": "placeholder",
        "name": name,
        "built_at": built_at,
        "investment_amount": float(blob.get("investment_amount", 0.0)),
        "strategies": list(blob.get("strategies", [])),
        "holdings": list(blob.get("holdings", [])),
        "selection_details": list(blob.get("selection_details", []) or []),
    }
    try:
        meta = create_snapshot(name, payload)
    except StorageError:
        quarantine(legacy_path, reason="legacy_invalid_payload")
        return None

    archived = legacy_path.with_name("saved_portfolio.legacy.json")
    shutil.move(str(legacy_path), str(archived))

    _migrate_legacy_history_csv(payload, meta.id)

    logger.info("migrated legacy portfolio to snapshot %s; archived %s", meta.id, archived)
    return meta


def _migrate_legacy_history_csv(payload: dict[str, Any], snapshot_id: str) -> None:
    """Move legacy ``portfolio_history.csv`` rows whose composition signature
    matches the imported snapshot to ``value_history/<snapshot_id>.csv``."""

    legacy_csv = get_data_dir() / "portfolio_history.csv"
    if not legacy_csv.exists():
        return
    try:
        import csv

        signature = _legacy_signature(payload.get("holdings", []))
        rows_to_keep: list[dict[str, Any]] = []
        with legacy_csv.open("r", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                if row.get("signature") == signature:
                    rows_to_keep.append({"date": row["date"], "value": row["portfolio_value"]})
        target = value_history_path(snapshot_id)
        if rows_to_keep:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=["date", "value"])
                writer.writeheader()
                writer.writerows(rows_to_keep)
            logger.info(
                "imported %d legacy history rows to %s", len(rows_to_keep), target
            )
        archived_csv = legacy_csv.with_name("portfolio_history.legacy.csv")
        shutil.move(str(legacy_csv), str(archived_csv))
    except Exception as exc:
        logger.warning("legacy history CSV migration failed: %s", exc)


def _legacy_signature(holdings: list[dict[str, Any]]) -> str:
    """Recreate the legacy composition signature used in ``portfolio.py``."""

    active = [
        (h.get("ticker", ""), int(h.get("shares", 0) or 0))
        for h in holdings
        if int(h.get("shares", 0) or 0) > 0
    ]
    if not active:
        return "empty"
    active.sort()
    return "|".join(f"{ticker}:{shares}" for ticker, shares in active)


# ---------------------------------------------------------------------------
# Helpers (exported for callers that need to iterate by name)
# ---------------------------------------------------------------------------
def find_by_name(name: str) -> SnapshotMeta | None:
    for meta in list_snapshots():
        if meta.name == name:
            return meta
    return None


def iter_snapshots() -> Iterable[dict[str, Any]]:
    for meta in list_snapshots():
        yield read_snapshot(meta.id)
