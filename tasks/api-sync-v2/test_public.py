import importlib.util
import os
import sqlite3
from pathlib import Path

import pytest


def candidate():
    path = Path(os.environ["BENCH_CANDIDATE"])
    spec = importlib.util.spec_from_file_location("candidate_v2_public", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def row(key, name=None):
    return {"id": key, "name": name or f"name-{key}", "updated_at": "2026-04-02T03:04:05+00:00"}


def test_sync_pages_commits_last_duplicate_once():
    app = candidate()
    db = sqlite3.connect(":memory:")
    pages = {None: {"items": [row("a")], "next_cursor": "p2"}, "p2": {"items": [row("a", "updated")], "next_cursor": None}}
    assert app.sync(db, lambda cursor: pages[cursor], lambda _: None) == 1
    assert db.execute("select name from records where id='a'").fetchone() == ("updated",)


def test_all_pages_are_validated_before_first_persistent_write():
    app = candidate()
    db = sqlite3.connect(":memory:")
    pages = iter([{"items": [row("first")], "next_cursor": "p2"}, {"items": [{"id": "bad"}], "next_cursor": None}])
    with pytest.raises(ValueError):
        app.sync(db, lambda _: next(pages), lambda _: None)
    assert db.execute("select name from sqlite_master where type='table' and name='records'").fetchone() is None


@pytest.mark.parametrize("kwargs", [{"max_attempts": True}, {"max_attempts": 0}, {"base_delay": -0.1}, {"base_delay": float("inf")}])
def test_invalid_retry_settings_fail_before_fetch(kwargs):
    app = candidate()
    called = False
    def fetch(_):
        nonlocal called
        called = True
        return {"items": [], "next_cursor": None}
    with pytest.raises(ValueError):
        app.sync(sqlite3.connect(":memory:"), fetch, lambda _: None, **kwargs)
    assert not called


def test_transient_error_retries_only_the_current_page():
    app = candidate()
    db = sqlite3.connect(":memory:")
    attempts = {None: 0, "p2": 0}
    sleeps = []
    def fetch(cursor):
        attempts[cursor] += 1
        if cursor == "p2" and attempts[cursor] == 1:
            raise app.TransientError("temporary")
        return {"items": [row("x")] if cursor is None else [], "next_cursor": "p2" if cursor is None else None}
    assert app.sync(db, fetch, sleeps.append, max_attempts=3, base_delay=0.25) == 1
    assert attempts == {None: 1, "p2": 2}
    assert sleeps == [0.25]


def test_caller_owned_transaction_is_rejected_without_fetch():
    app = candidate()
    db = sqlite3.connect(":memory:")
    db.execute("insert into sqlite_master select 1") if False else None
    db.execute("create table existing(value text)")
    db.execute("insert into existing values('pending')")
    called = False
    def fetch(_):
        nonlocal called
        called = True
        return {"items": [], "next_cursor": None}
    with pytest.raises(ValueError):
        app.sync(db, fetch, lambda _: None)
    assert not called
    assert db.in_transaction
