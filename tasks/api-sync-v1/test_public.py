import importlib.util
import os
import sqlite3
from pathlib import Path

import pytest


def candidate():
    path = Path(os.environ["BENCH_CANDIDATE"])
    spec = importlib.util.spec_from_file_location("candidate_v1", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def page(items, next_cursor=None):
    return {"items": items, "next_cursor": next_cursor}


def item(value):
    return {"id": value, "name": f"name-{value}", "updated_at": "2026-01-01T12:00:00+00:00"}


def test_sync_pages_upserts_and_resolves_duplicates():
    app = candidate()
    db = sqlite3.connect(":memory:")
    calls = []
    pages = {None: page([item("a")], "next"), "next": page([dict(item("a"), name="last")])}
    count = app.sync(db, lambda cursor: calls.append(cursor) or pages[cursor], lambda _: None)
    assert count == 1
    assert calls == [None, "next"]
    assert db.execute("select name from records where id='a'").fetchone() == ("last",)


def test_sync_rolls_back_when_fetch_fails():
    app = candidate()
    db = sqlite3.connect(":memory:")
    db.execute("create table records(id text primary key, name text not null, updated_at text not null)")
    db.execute("insert into records values('old', 'kept', '2026-01-01T00:00:00+00:00')")
    db.commit()
    def fetch(cursor):
        if cursor is None:
            return page([item("new")], "again")
        raise RuntimeError("transport failure")
    with pytest.raises(RuntimeError):
        app.sync(db, fetch, lambda _: None)
    assert db.execute("select id from records order by id").fetchall() == [("old",)]


@pytest.mark.parametrize("kwargs", [{"max_attempts": True}, {"max_attempts": 0}, {"base_delay": -1}, {"base_delay": float("nan")}])
def test_sync_rejects_invalid_retry_configuration(kwargs):
    app = candidate()
    called = False
    def fetch(_):
        nonlocal called
        called = True
        return page([])
    with pytest.raises(ValueError):
        app.sync(sqlite3.connect(":memory:"), fetch, lambda _: None, **kwargs)
    assert not called
