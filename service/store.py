"""SQLite-backed campaign store.

The on-disk persistence layer for the service.  Each campaign lives
in one row of one table; the row's ``bundle_json`` column holds the
JSON bundle produced by :mod:`service.serialize`, so persistence
shares its encoding with the ``/campaigns/{id}/export`` and
``/campaigns/import`` endpoints.

Why SQLite, not flat files (v0.1-v0.3 behaviour)?

- **Atomic writes.**  An UPDATE inside a transaction either commits
  in full or leaves the previous row intact; the old tree-of-files
  approach could be interrupted mid-write and leave a partial
  campaign on disk.
- **No `/tmp` fragility.**  The default db path lives wherever the
  operator points ``HYPLAN_CAMPAIGNS_DB`` at — a persistent volume,
  a shared mount, anywhere.
- **Single artifact to back up, ship, or mount read-only.**

Backwards compatibility: on first startup against a fresh database,
:func:`migrate_filesystem_to_db` imports any campaigns that still
live under the legacy ``HYPLAN_CAMPAIGNS_DIR`` directory tree.  This
runs idempotently — subsequent restarts no-op once the campaigns
are in the db.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Iterator

from hyplan.campaign import Campaign

from .serialize import campaign_to_bundle, bundle_to_campaign

logger = logging.getLogger("hyplan-service")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id  TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    bounds_json  TEXT NOT NULL,
    revision     INTEGER NOT NULL DEFAULT 0,
    bundle_json  TEXT NOT NULL,
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS campaigns_name_idx ON campaigns(name);
CREATE INDEX IF NOT EXISTS campaigns_updated_at_idx ON campaigns(updated_at);
"""


def _connect(db_path: str) -> sqlite3.Connection:
    """Open the SQLite db, ensuring the schema is in place."""
    # check_same_thread=False because FastAPI may dispatch a request
    # to a worker thread.  Single-writer assumption holds since
    # routes are sequential under the standard uvicorn worker model;
    # if we move to multi-worker, switch to a connection-per-request
    # pattern.
    conn = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")     # better concurrent reads
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    return conn


# Module-level connection.  Initialized by init_store() at app
# startup or lazily on first use.  Tests typically reset this via
# the conftest autouse fixture.
_conn: sqlite3.Connection | None = None
_db_path: str | None = None


def init_store(db_path: str) -> None:
    """Open (or create) the campaign db at ``db_path``."""
    global _conn, _db_path
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    _conn = _connect(db_path)
    _db_path = db_path
    logger.info("Campaign store opened at %s", db_path)


def _require_conn() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError(
            "Campaign store not initialized.  Call init_store(path) first."
        )
    return _conn


def save_campaign(campaign: Campaign, *, service_version: str = "0.4.0") -> None:
    """Insert-or-replace one campaign row from its current in-memory state."""
    conn = _require_conn()
    bundle = campaign_to_bundle(campaign, service_version=service_version)
    conn.execute(
        """
        INSERT OR REPLACE INTO campaigns
            (campaign_id, name, bounds_json, revision, bundle_json, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            campaign.campaign_id,
            campaign.name,
            json.dumps(list(campaign.bounds)),
            int(campaign.revision),
            json.dumps(bundle),
        ),
    )
    logger.info("Persisted campaign '%s' (%s) to store", campaign.name, campaign.campaign_id)


def load_campaign(campaign_id: str) -> Campaign | None:
    """Return the Campaign with ``campaign_id`` from the db, or ``None``."""
    conn = _require_conn()
    row = conn.execute(
        "SELECT bundle_json FROM campaigns WHERE campaign_id = ?",
        (campaign_id,),
    ).fetchone()
    if row is None:
        return None
    bundle = json.loads(row[0])
    return bundle_to_campaign(bundle)


def iter_campaigns() -> Iterator[Campaign]:
    """Yield every campaign in the db, oldest-updated first."""
    conn = _require_conn()
    cur = conn.execute(
        "SELECT campaign_id, bundle_json FROM campaigns ORDER BY updated_at ASC"
    )
    for campaign_id, bundle_json in cur:
        try:
            yield bundle_to_campaign(json.loads(bundle_json))
        except Exception as exc:
            logger.warning(
                "Failed to deserialize campaign %s from store: %s",
                campaign_id, exc,
            )


def list_campaign_meta() -> list[dict]:
    """Return a lightweight summary row per campaign (no full bundle)."""
    conn = _require_conn()
    rows = conn.execute(
        "SELECT campaign_id, name, bounds_json, revision, updated_at "
        "FROM campaigns ORDER BY updated_at DESC"
    ).fetchall()
    return [
        {
            "campaign_id": cid,
            "name": name,
            "bounds": json.loads(bounds_json),
            "revision": revision,
            "updated_at": updated_at,
        }
        for cid, name, bounds_json, revision, updated_at in rows
    ]


def delete_campaign(campaign_id: str) -> bool:
    """Delete a campaign row; return True if a row was removed."""
    conn = _require_conn()
    cur = conn.execute(
        "DELETE FROM campaigns WHERE campaign_id = ?", (campaign_id,),
    )
    return cur.rowcount > 0


def migrate_filesystem_to_db(legacy_dir: str) -> int:
    """One-shot migration of the legacy flat-file campaign tree.

    Walks ``legacy_dir`` for ``<uuid>/campaign.json`` entries that
    aren't already in the db and ``Campaign.load()``s + saves each.
    Returns the count of imported campaigns.  Safe to call on every
    startup — re-imports are no-ops.
    """
    if not os.path.isdir(legacy_dir):
        return 0
    conn = _require_conn()
    imported = 0
    for entry in sorted(os.listdir(legacy_dir)):
        campaign_dir = os.path.join(legacy_dir, entry)
        if not os.path.isfile(os.path.join(campaign_dir, "campaign.json")):
            continue
        # Skip if already in the store.
        existing = conn.execute(
            "SELECT 1 FROM campaigns WHERE campaign_id = ? LIMIT 1", (entry,),
        ).fetchone()
        if existing is not None:
            continue
        try:
            campaign = Campaign.load(campaign_dir)
            save_campaign(campaign)
            imported += 1
            logger.info(
                "Migrated legacy campaign '%s' (%s) into store",
                campaign.name, campaign.campaign_id,
            )
        except Exception as exc:
            logger.warning(
                "Skipping legacy campaign dir %s: %s", campaign_dir, exc,
            )
    return imported
