import importlib.util
import json
import os
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest


def candidate():
    path = Path(os.environ["BENCH_CANDIDATE"])
    spec = importlib.util.spec_from_file_location("candidate_v2_final", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HttpResponse:
    def __init__(self, body):
        self.body = body
        self.closed = False
    def read(self):
        return json.dumps(self.body).encode("utf-8")
    def close(self):
        self.closed = True
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.close()


class TrackedConnection:
    def __init__(self, connection):
        self.connection = connection
        self.closed = False
    def __getattr__(self, name):
        return getattr(self.connection, name)
    def close(self):
        self.closed = True
        return self.connection.close()


def test_real_cli_to_fetch_path_with_only_transport_replaced(monkeypatch, tmp_path, capsys):
    app = candidate()
    requests = []
    responses = [
        HttpResponse({"items": [{"id": "1", "name": "one", "updated_at": "2026-01-01T00:00:00+00:00"}], "next_cursor": "a b"}),
        HttpResponse({"items": [{"id": "2", "name": "two", "updated_at": "2026-01-02T00:00:00+00:00"}], "next_cursor": None}),
    ]
    connections = []
    real_connect = sqlite3.connect
    def connect(*args, **kwargs):
        tracked = TrackedConnection(real_connect(*args, **kwargs))
        connections.append(tracked)
        return tracked
    def urlopen(request, *args, **kwargs):
        requests.append(request)
        return responses[len(requests) - 1]
    monkeypatch.setattr(app, "urlopen", urlopen)
    monkeypatch.setattr(app.sqlite3, "connect", connect)
    db_path = tmp_path / "result.sqlite"
    code = app.main(["--url", "http://example.invalid/sync?tenant=blue", "--db", str(db_path)])
    assert code == 0
    assert len(requests) == 2
    assert all(request.get_method() == "GET" for request in requests)
    assert parse_qs(urlparse(requests[0].full_url).query) == {"tenant": ["blue"]}
    assert parse_qs(urlparse(requests[1].full_url).query) == {"tenant": ["blue"], "cursor": ["a b"]}
    assert all(response.closed for response in responses)
    assert len(connections) == 1 and connections[0].closed
    check = real_connect(db_path)
    assert check.execute("select id from records order by id").fetchall() == [("1",), ("2",)]
    output = capsys.readouterr()
    assert "completed" in (output.out + output.err).lower()
    assert "example.invalid" not in (output.out + output.err)
    assert "blue" not in (output.out + output.err)
    check.close()


def test_invalid_cli_configuration_returns_two_before_network(monkeypatch, capsys):
    app = candidate()
    monkeypatch.delenv("SYNC_URL", raising=False)
    monkeypatch.delenv("SYNC_DB", raising=False)
    monkeypatch.setattr(app, "urlopen", lambda *a, **k: pytest.fail("network must not be touched"))
    assert app.main([]) == 2
    captured = capsys.readouterr()
    assert "required" in (captured.out + captured.err).lower()


def test_later_page_transport_failure_rolls_back_everything(monkeypatch, tmp_path):
    app = candidate()
    db_path = tmp_path / "rollback.sqlite"
    db = sqlite3.connect(db_path)
    db.execute("create table records(id text primary key, name text not null, updated_at text not null)")
    db.execute("insert into records values('old', 'keep', '2026-01-01T00:00:00+00:00')")
    db.commit()
    db.close()
    calls = 0
    def urlopen(request, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return HttpResponse({"items": [{"id": "new", "name": "new", "updated_at": "2026-01-02T00:00:00+00:00"}], "next_cursor": "next"})
        raise OSError("simulated transport loss")
    monkeypatch.setattr(app, "urlopen", urlopen)
    assert app.main(["--url", "http://example.invalid/sync", "--db", str(db_path)]) == 1
    check = sqlite3.connect(db_path)
    assert check.execute("select id,name from records").fetchall() == [("old", "keep")]
    check.close()


def test_sync_rolls_back_creation_of_new_table_on_database_failure():
    app = candidate()
    db = sqlite3.connect(":memory:")
    def fetch(_):
        return {"items": [{"id": "x", "name": "y", "updated_at": "2026-01-01T00:00:00+00:00"}], "next_cursor": None}
    def reject_insert(action, arg1, arg2, database, trigger):
        if action == sqlite3.SQLITE_INSERT and arg1 == "records":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    db.set_authorizer(reject_insert)
    with pytest.raises(Exception):
        app.sync(db, fetch, lambda _: None)
    assert db.execute("select name from sqlite_master where type='table' and name='records'").fetchone() is None
