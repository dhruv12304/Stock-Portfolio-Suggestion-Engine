"""Unit tests for api.storage.

Coverage maps to BUG_REPORT.md:
- B1 path consistency (the module owns its own path resolution).
- B2 quarantine instead of delete on validation failure.
- B3 explicit save vs build; multiple named snapshots coexist.
- B4 data/ directories are created on demand.
- Section F directory layout end to end.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api import storage


def test_ensure_dirs_creates_layout(isolated_data_dir: Path) -> None:
    storage.ensure_dirs()
    assert storage.portfolios_dir().is_dir()
    assert storage.value_history_dir().is_dir()


def test_create_snapshot_writes_file_and_updates_index(sample_payload, isolated_data_dir):
    meta = storage.create_snapshot("My first portfolio", sample_payload)

    assert meta.name == "My first portfolio"
    assert (isolated_data_dir / "portfolios" / f"{meta.id}.json").exists()

    index = storage.read_index()
    assert index.active_id == meta.id
    assert len(index.items) == 1
    assert index.items[0].id == meta.id


def test_create_snapshot_does_not_overwrite_active_when_one_exists(sample_payload):
    first = storage.create_snapshot("First", sample_payload)
    second = storage.create_snapshot("Second", sample_payload)

    index = storage.read_index()
    # First snapshot stays active; the second build does not silently take over.
    assert index.active_id == first.id
    assert {m.id for m in index.items} == {first.id, second.id}


def test_set_active_validates_id(sample_payload):
    storage.create_snapshot("Only", sample_payload)
    with pytest.raises(storage.SnapshotNotFoundError):
        storage.set_active("does-not-exist")


def test_read_snapshot_round_trip(sample_payload):
    meta = storage.create_snapshot("RT", sample_payload)
    loaded = storage.read_snapshot(meta.id)

    assert loaded["id"] == meta.id
    assert loaded["name"] == "RT"
    assert loaded["holdings"][0]["ticker"] == "AAPL"
    assert loaded["schema_version"] == storage.SCHEMA_VERSION


def test_read_snapshot_quarantines_invalid_blob(isolated_data_dir, sample_payload):
    meta = storage.create_snapshot("BadOne", sample_payload)
    snap_path = isolated_data_dir / "portfolios" / f"{meta.id}.json"

    # Corrupt the file by dropping required keys.
    snap_path.write_text(json.dumps({"id": meta.id, "name": "BadOne"}))

    with pytest.raises(storage.StorageError):
        storage.read_snapshot(meta.id)

    # Source path is gone; a quarantine sibling exists.
    assert not snap_path.exists()
    siblings = list((isolated_data_dir / "portfolios").glob(f"{meta.id}.quarantine-*.json"))
    assert len(siblings) == 1

    # Index has been pruned.
    index = storage.read_index()
    assert all(m.id != meta.id for m in index.items)


def test_read_index_handles_corrupt_file(isolated_data_dir, sample_payload):
    storage.create_snapshot("OK", sample_payload)
    index_file = isolated_data_dir / "portfolios" / "index.json"
    index_file.write_text("not valid json")

    # Bad index file is quarantined; the function returns an empty Index.
    fresh = storage.read_index()
    assert fresh.items == []
    assert fresh.active_id is None
    siblings = list((isolated_data_dir / "portfolios").glob("index.quarantine-*.json"))
    assert len(siblings) == 1


def test_atomic_write_does_not_leave_temp_files(isolated_data_dir, sample_payload):
    storage.create_snapshot("A", sample_payload)
    storage.create_snapshot("B", sample_payload)

    leftover = list((isolated_data_dir / "portfolios").glob(".*.tmp"))
    assert leftover == []


def test_update_snapshot_updates_payload_and_timestamp(sample_payload):
    meta = storage.create_snapshot("U1", sample_payload)
    new_payload = dict(sample_payload)
    new_payload["investment_amount"] = 50000.0

    updated_meta = storage.update_snapshot(meta.id, new_payload)
    assert updated_meta.updated_at >= meta.updated_at

    loaded = storage.read_snapshot(meta.id)
    assert loaded["investment_amount"] == 50000.0
    assert loaded["name"] == "U1"  # name preserved across updates
    assert loaded["id"] == meta.id


def test_rename_snapshot(sample_payload):
    meta = storage.create_snapshot("old name", sample_payload)
    new_meta = storage.rename_snapshot(meta.id, "new name")

    assert new_meta.name == "new name"
    loaded = storage.read_snapshot(meta.id)
    assert loaded["name"] == "new name"


def test_duplicate_snapshot(sample_payload):
    original = storage.create_snapshot("Source", sample_payload)
    clone = storage.duplicate_snapshot(original.id)

    assert clone.id != original.id
    assert clone.name == "Source (copy)"
    loaded = storage.read_snapshot(clone.id)
    assert loaded["holdings"] == sample_payload["holdings"]


def test_delete_snapshot_removes_file_and_history(isolated_data_dir, sample_payload):
    meta = storage.create_snapshot("D1", sample_payload)
    history_path = isolated_data_dir / "value_history" / f"{meta.id}.csv"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text("date,value\n2026-05-16,1\n")

    storage.delete_snapshot(meta.id)

    assert not (isolated_data_dir / "portfolios" / f"{meta.id}.json").exists()
    assert not history_path.exists()
    assert all(m.id != meta.id for m in storage.list_snapshots())


def test_upsert_value_history_creates_and_updates_one_row_per_date(sample_payload):
    meta = storage.create_snapshot("History", sample_payload)

    storage.upsert_value_history(
        meta.id,
        date="2026-05-15",
        value=10123.45,
        as_of="2026-05-15T16:00:00-04:00",
    )
    storage.upsert_value_history(
        meta.id,
        date="2026-05-15",
        value=10199.99,
        as_of="2026-05-15T16:05:00-04:00",
    )
    storage.upsert_value_history(meta.id, date="2026-05-16", value=10210.00)

    rows = storage.read_value_history(meta.id)
    assert len(rows) == 2
    assert rows[0]["date"] == "2026-05-15"
    assert rows[0]["value"] == pytest.approx(10199.99)
    assert rows[0]["as_of"] == "2026-05-15T16:05:00-04:00"
    assert rows[1]["date"] == "2026-05-16"
    assert rows[1]["value"] == pytest.approx(10210.00)


def test_delete_active_snapshot_promotes_next(sample_payload):
    first = storage.create_snapshot("First", sample_payload)
    second = storage.create_snapshot("Second", sample_payload)
    storage.set_active(second.id)
    storage.delete_snapshot(second.id)

    index = storage.read_index()
    assert index.active_id == first.id


def test_delete_only_snapshot_clears_active(sample_payload):
    meta = storage.create_snapshot("Solo", sample_payload)
    storage.delete_snapshot(meta.id)

    index = storage.read_index()
    assert index.items == []
    assert index.active_id is None


def test_migrate_legacy_creates_snapshot_and_archives_file(isolated_data_dir, sample_payload):
    legacy_payload = {
        "built_at": "2026-05-09T12:00:00",
        "investment_amount": 10000.0,
        "strategies": ["Index Investing"],
        "holdings": [
            {"ticker": "VTI", "name": "Vanguard Total Stock Market ETF",
             "shares": 30, "initial_price": 220.0, "weight": 0.6}
        ],
    }
    legacy_path = isolated_data_dir / "saved_portfolio.json"
    legacy_path.write_text(json.dumps(legacy_payload))

    meta = storage.migrate_legacy_if_needed(legacy_path)
    assert meta is not None
    assert meta.name.startswith("Imported ")

    # Legacy file is archived, never deleted.
    assert not legacy_path.exists()
    archived = isolated_data_dir / "saved_portfolio.legacy.json"
    assert archived.exists()

    loaded = storage.read_snapshot(meta.id)
    assert loaded["holdings"][0]["ticker"] == "VTI"


def test_migrate_legacy_returns_none_when_no_file():
    assert storage.migrate_legacy_if_needed() is None


def test_migrate_legacy_imports_history_csv(isolated_data_dir, sample_payload):
    """Section F migration: legacy portfolio_history.csv rows that match the
    imported snapshot's composition signature land in value_history/<id>.csv."""

    legacy_holdings = [
        {"ticker": "AAPL", "name": "Apple", "shares": 30, "initial_price": 200.0, "weight": 0.6},
        {"ticker": "MSFT", "name": "Microsoft", "shares": 10, "initial_price": 400.0, "weight": 0.4},
    ]
    legacy_path = isolated_data_dir / "saved_portfolio.json"
    legacy_path.write_text(json.dumps({
        "built_at": "2026-05-09T12:00:00",
        "investment_amount": 10000.0,
        "strategies": ["Quality Investing"],
        "holdings": legacy_holdings,
    }))

    signature = "AAPL:30|MSFT:10"
    legacy_csv = isolated_data_dir / "portfolio_history.csv"
    legacy_csv.write_text(
        "date,timestamp,signature,portfolio_value\n"
        f"2026-05-09,2026-05-09T16:00:00,{signature},10100.0\n"
        f"2026-05-10,2026-05-10T16:00:00,{signature},10250.5\n"
        f"2026-05-11,2026-05-11T16:00:00,OTHER:99,5000.0\n"
    )

    meta = storage.migrate_legacy_if_needed(legacy_path)
    assert meta is not None

    migrated_csv = isolated_data_dir / "value_history" / f"{meta.id}.csv"
    assert migrated_csv.exists()
    contents = migrated_csv.read_text()
    assert "2026-05-09,10100.0" in contents
    assert "2026-05-10,10250.5" in contents
    assert "OTHER" not in contents  # row with different signature is dropped

    # Both legacy files are archived, not deleted.
    assert (isolated_data_dir / "saved_portfolio.legacy.json").exists()
    assert (isolated_data_dir / "portfolio_history.legacy.csv").exists()
    assert not legacy_path.exists()
    assert not legacy_csv.exists()


def test_quarantine_preserves_payload(isolated_data_dir):
    path = isolated_data_dir / "portfolios" / "broken.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "{ partial corrupted"
    path.write_text(payload)

    target = storage.quarantine(path)
    assert target is not None
    assert target.exists()
    assert target.read_text() == payload
    assert not path.exists()
