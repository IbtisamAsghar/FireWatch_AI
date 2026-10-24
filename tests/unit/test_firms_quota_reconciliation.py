import pytest

from firewatch.sources.firms import FirmsError, QuotaLedger


def test_fresh_provider_counter_replaces_only_completed_prior_requests(tmp_path):
    ledger = QuotaLedger(tmp_path / "quota.sqlite3", "key", 4000)
    completed = ledger.reserve(1000, 100)
    ledger.settle(completed, 101)
    ledger.observe(10, 102)
    ledger.reserve(3990, 103)
    with pytest.raises(FirmsError):
        ledger.reserve(1, 104)


def test_inflight_and_later_completions_keep_full_reservations(tmp_path):
    ledger = QuotaLedger(tmp_path / "quota.sqlite3", "key", 4000)
    ledger.reserve(1000, 100)
    late = ledger.reserve(1000, 101)
    ledger.settle(late, 104)
    ledger.observe(10, 103)
    with pytest.raises(FirmsError):
        ledger.reserve(2000, 105)


def test_unknown_legacy_reservations_expire_without_being_reconciled(tmp_path):
    path = tmp_path / "quota.sqlite3"
    ledger = QuotaLedger(path, "key", 4000)
    with ledger._connect() as connection:
        connection.execute("INSERT INTO usage VALUES ('key',100,4000)")
    ledger.observe(0, 101)
    with pytest.raises(FirmsError):
        ledger.reserve(1, 102)
    ledger.reserve(4000, 701)


def test_old_provider_response_cannot_lower_newer_observation(tmp_path):
    ledger = QuotaLedger(tmp_path / "quota.sqlite3", "key", 4000)
    ledger.observe(3500, 103)
    ledger.observe(0, 102)
    with pytest.raises(FirmsError):
        ledger.reserve(501, 104)


def test_reconciliation_keeps_cooldown_and_other_keys(tmp_path):
    path = tmp_path / "quota.sqlite3"
    first = QuotaLedger(path, "first", 4000)
    second = QuotaLedger(path, "second", 4000)
    token = first.reserve(1000, 100)
    second.reserve(4000, 100)
    first.settle(token, 101)
    first.defer(200)
    first.observe(0, 102)
    with pytest.raises(FirmsError):
        first.reserve(1, 103)
    with pytest.raises(FirmsError):
        second.reserve(1, 103)


def test_expired_completion_token_cannot_settle_reused_rowid(tmp_path):
    path = tmp_path / "quota.sqlite3"
    ledger = QuotaLedger(path, "key", 4000)
    old = ledger.reserve(4000, 100)
    ledger.reserve(4000, 701)
    ledger.settle(old, 702)
    ledger.observe(0, 703)
    with pytest.raises(FirmsError):
        ledger.reserve(1, 704)


def test_invalid_observation_never_releases_reservation(tmp_path):
    ledger = QuotaLedger(tmp_path / "quota.sqlite3", "key", 4000)
    token = ledger.reserve(4000, 100)
    ledger.settle(token, 101)
    with pytest.raises(ValueError):
        ledger.observe(True, 102)
    with pytest.raises(FirmsError):
        ledger.reserve(1, 103)
