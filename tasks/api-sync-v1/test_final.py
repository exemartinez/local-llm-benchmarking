import importlib.util
import os
import sqlite3
from pathlib import Path

import pytest


def candidate():
    path = Path(os.environ["BENCH_CANDIDATE"])
    spec = importlib.util.spec_from_file_location("candidate_v1_final", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_caller_transaction_is_not_taken_over():
    app = candidate()
    db = sqlite3.connect(":memory:")
    db.execute("create table records(id text primary key, name text not null, updated_at text not null)")
    db.execute("insert into records values('pending', 'x', '2026-01-01T00:00:00+00:00')")
    with pytest.raises(ValueError):
        app.sync(db, lambda _: pytest.fail("must reject before fetching"), lambda _: None)
    assert db.in_transaction


def test_repeated_cursor_is_rejected_before_database_changes():
    app = candidate()
    db = sqlite3.connect(":memory:")
    sequence = iter([{"items": [], "next_cursor": "same"}, {"items": [], "next_cursor": "same"}])
    with pytest.raises(ValueError):
        app.sync(db, lambda _: next(sequence), lambda _: None)
    assert db.execute("select name from sqlite_master where type='table' and name='records'").fetchone() is None


def test_http_fetcher_preserves_existing_query_and_encodes_cursor(monkeypatch):
    app = candidate()
    from urllib.parse import parse_qs, urlparse
    captured = {}
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return b'{"items":[],"next_cursor":null}'
    def fake_open(request, *args, **kwargs):
        captured["request"] = request
        return Response()
    if hasattr(app, "urlopen"):
        monkeypatch.setattr(app, "urlopen", fake_open)
    else:
        monkeypatch.setattr(app.urllib.request, "urlopen", fake_open)
    app.fetch_http_page("http://example.invalid/sync?tenant=x", "a b&c")
    url = captured["request"].full_url
    assert parse_qs(urlparse(url).query) == {"tenant": ["x"], "cursor": ["a b&c"]}


def test_historical_final_suite_does_not_claim_production_readiness():
    assert "not a production-readiness" in (Path(__file__).with_name("SPEC.md").read_text(encoding="utf-8"))
