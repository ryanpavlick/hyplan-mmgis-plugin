"""Service-level tests for the SQLite-backed campaign store.

These exercise the persistence layer directly (without the FastAPI
layer) plus a few cross-restart scenarios via the TestClient.
"""

from __future__ import annotations

from service import store


def test_store_round_trip_a_campaign(tmp_path):
    """save_campaign -> load_campaign returns an equivalent Campaign."""
    from hyplan.campaign import Campaign
    store.init_store(str(tmp_path / "rt.sqlite"))
    c = Campaign(name="Round trip", bounds=(-120.5, 35.0, -119.5, 35.5))
    store.save_campaign(c)

    loaded = store.load_campaign(c.campaign_id)
    assert loaded is not None
    assert loaded.campaign_id == c.campaign_id
    assert loaded.name == c.name
    assert tuple(loaded.bounds) == tuple(c.bounds)


def test_store_load_missing_returns_none(tmp_path):
    store.init_store(str(tmp_path / "miss.sqlite"))
    assert store.load_campaign("not-a-real-id") is None


def test_store_replace_keeps_single_row(tmp_path):
    """A second save_campaign with the same id overwrites in place
    (UPSERT) rather than producing a duplicate row.

    HyPlan exposes ``Campaign.name`` as a read-only property, so the
    cheapest way to mutate something between saves is to add a flight
    line via the campaign's own API.
    """
    from hyplan.campaign import Campaign
    from hyplan.flight_line import FlightLine
    from hyplan.units import ureg
    store.init_store(str(tmp_path / "rep.sqlite"))
    c = Campaign(name="Re", bounds=(-1.0, -1.0, 1.0, 1.0))
    store.save_campaign(c)
    rev_before = c.revision

    c.add_flight_lines(
        [FlightLine.from_endpoints(0.1, 0.1, 0.2, 0.2,
                                    altitude_msl=3000 * ureg.meter,
                                    site_name="line_for_upsert")],
        group_name="upsert-test", group_type="manual",
    )
    store.save_campaign(c)

    rows = store.list_campaign_meta()
    assert len(rows) == 1                 # still one row (upsert worked)
    assert rows[0]["revision"] > rev_before


def test_store_iter_yields_all(tmp_path):
    from hyplan.campaign import Campaign
    store.init_store(str(tmp_path / "iter.sqlite"))
    for i in range(3):
        store.save_campaign(Campaign(name=f"C{i}", bounds=(0, 0, 1, 1)))

    ids = [c.campaign_id for c in store.iter_campaigns()]
    assert len(ids) == 3
    assert len(set(ids)) == 3   # all distinct


def test_store_delete_removes_row(tmp_path):
    from hyplan.campaign import Campaign
    store.init_store(str(tmp_path / "del.sqlite"))
    c = Campaign(name="Doomed", bounds=(0, 0, 1, 1))
    store.save_campaign(c)
    assert store.delete_campaign(c.campaign_id) is True
    assert store.load_campaign(c.campaign_id) is None
    assert store.delete_campaign(c.campaign_id) is False   # already gone


def test_migration_from_legacy_filesystem_dir(tmp_path):
    """migrate_filesystem_to_db imports old-style <id>/campaign.json trees."""
    from hyplan.campaign import Campaign
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    # Two legacy campaigns saved with the old Campaign.save() layout.
    c1 = Campaign(name="Legacy A", bounds=(0, 0, 1, 1))
    c1.save(str(legacy / c1.campaign_id))
    c2 = Campaign(name="Legacy B", bounds=(10, 10, 11, 11))
    c2.save(str(legacy / c2.campaign_id))
    # A stray non-campaign directory should be ignored.
    (legacy / "not_a_campaign").mkdir()

    store.init_store(str(tmp_path / "mig.sqlite"))
    count = store.migrate_filesystem_to_db(str(legacy))
    assert count == 2

    # Re-running is idempotent.
    count = store.migrate_filesystem_to_db(str(legacy))
    assert count == 0

    ids = {c.campaign_id for c in store.iter_campaigns()}
    assert c1.campaign_id in ids
    assert c2.campaign_id in ids


def test_migration_no_legacy_dir_is_no_op(tmp_path):
    """No legacy dir -> no migration -> no errors."""
    store.init_store(str(tmp_path / "nomig.sqlite"))
    assert store.migrate_filesystem_to_db(str(tmp_path / "does-not-exist")) == 0


def test_persistence_survives_in_memory_clear(client, campaign_with_lines):
    """Clearing the in-memory cache then re-hydrating from the store
    yields the same campaign.  This is the path taken on every
    service restart."""
    from service import state

    # Sanity: campaign is in memory after the fixture ran.
    listing_before = client.get("/campaigns").json()["campaigns"]
    ids_before = {c["campaign_id"] for c in listing_before}
    assert campaign_with_lines in ids_before

    # Simulate a restart: clear in-memory state, re-hydrate from store.
    state._campaigns.clear()
    state._plans.clear()
    state.load_persisted_campaigns()

    listing_after = client.get("/campaigns").json()["campaigns"]
    ids_after = {c["campaign_id"] for c in listing_after}
    assert campaign_with_lines in ids_after

    # The fully-hydrated campaign also serves via /campaigns/{id}.
    resp = client.get(f"/campaigns/{campaign_with_lines}")
    assert resp.status_code == 200
    assert len(resp.json()["flight_lines"]["features"]) > 0


def test_list_campaigns_endpoint_returns_summary(client, campaign_with_lines):
    """GET /campaigns returns lightweight metadata (no full bundles)."""
    resp = client.get("/campaigns")
    assert resp.status_code == 200
    body = resp.json()
    assert "campaigns" in body
    found = next(
        (c for c in body["campaigns"] if c["campaign_id"] == campaign_with_lines),
        None,
    )
    assert found is not None
    # Shape: id, name, bounds, revision, updated_at — no flight_lines blob.
    assert set(found.keys()) >= {"campaign_id", "name", "bounds", "revision", "updated_at"}
    assert "flight_lines" not in found


def test_state_module_exposes_both_env_paths():
    """state module declares both CAMPAIGNS_DB and CAMPAIGNS_DIR.

    The per-test ``isolate_state`` fixture monkeypatches these to a
    tmp_path, so we can't usefully assert they match the session-level
    env vars here.  We only assert the constants exist and are
    string-ish, which the rest of the persistence layer relies on.
    """
    from service import state
    assert isinstance(state.CAMPAIGNS_DB, str) and state.CAMPAIGNS_DB
    assert isinstance(state.CAMPAIGNS_DIR, str) and state.CAMPAIGNS_DIR
