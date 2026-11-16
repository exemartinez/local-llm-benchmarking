"""Small trusted fixture used only to validate the packaged task test suites."""
import argparse
import json
import logging
import math
import os
import sqlite3
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


class TransientError(Exception):
    pass


def _validate_page(page):
    if not isinstance(page, dict) or not {"items", "next_cursor"}.issubset(page):
        raise ValueError("Invalid page")
    items, cursor = page["items"], page["next_cursor"]
    if not isinstance(items, list) or (cursor is not None and (not isinstance(cursor, str) or not cursor)):
        raise ValueError("Invalid page")
    from datetime import datetime
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Invalid record")
        for key in ("id", "name", "updated_at"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise ValueError("Invalid record")
        try:
            stamp = datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Invalid timestamp") from exc
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("Timestamp needs timezone")
    return items, cursor


def sync(connection, fetch_page, sleep, *, max_attempts=3, base_delay=0.1, logger=None):
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer")
    if not isinstance(base_delay, (int, float)) or isinstance(base_delay, bool) or not math.isfinite(base_delay) or base_delay < 0:
        raise ValueError("base_delay must be finite and nonnegative")
    if connection.in_transaction:
        raise ValueError("Caller owns an active transaction")
    all_rows, cursor, seen = [], None, set()
    while True:
        for attempt in range(1, max_attempts + 1):
            try:
                page = fetch_page(cursor)
                break
            except TransientError:
                if attempt == max_attempts:
                    raise
                sleep(base_delay * 2 ** (attempt - 1))
        items, next_cursor = _validate_page(page)
        all_rows.extend((item["id"], item["name"], item["updated_at"]) for item in items)
        if next_cursor is None:
            break
        if next_cursor in seen:
            raise ValueError("Repeated cursor")
        seen.add(next_cursor)
        cursor = next_cursor
    deduped = {}
    for row in all_rows:
        deduped[row[0]] = row
    try:
        connection.execute("BEGIN")
        connection.execute("CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, name TEXT NOT NULL, updated_at TEXT NOT NULL)")
        connection.executemany("INSERT INTO records(id,name,updated_at) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, updated_at=excluded.updated_at", deduped.values())
        connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    if logger:
        logger.info("Sync completed: %d records", len(deduped))
    return len(deduped)


def fetch_http_page(url, cursor):
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    if cursor is not None:
        query.append(("cursor", cursor))
    request = Request(urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)), method="GET")
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 429 or exc.code >= 500:
            raise TransientError("Transient HTTP failure") from exc
        raise
    except URLError as exc:
        raise TransientError("Transient transport failure") from exc


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.environ.get("SYNC_URL"))
    parser.add_argument("--db", default=os.environ.get("SYNC_DB"))
    parser.add_argument("--max-attempts", type=int, default=os.environ.get("SYNC_MAX_ATTEMPTS", "3"))
    parser.add_argument("--base-delay", type=float, default=os.environ.get("SYNC_BASE_DELAY", "0.1"))
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    if not args.url or not args.db or args.max_attempts < 1 or args.base_delay < 0 or not math.isfinite(args.base_delay):
        print("Invalid configuration: --url and --db are required; retry options must be valid.", file=sys.stderr)
        return 2
    connection = None
    try:
        connection = sqlite3.connect(args.db)
        count = sync(connection, lambda cursor: fetch_http_page(args.url, cursor), __import__("time").sleep,
                     max_attempts=args.max_attempts, base_delay=args.base_delay, logger=logging.getLogger(__name__))
        print(f"Sync completed: {count} records", file=sys.stderr)
        return 0
    except Exception as exc:
        print(f"Sync failed ({type(exc).__name__}).", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()
