# API-to-SQLite synchronization task — v1 historical baseline

This is the historical task contract retained for continuity. It is one coding problem, not a production-readiness certification.

Implement `candidate.py` with these functions:

```python
sync(connection, fetch_page, sleep, *, max_attempts=3, base_delay=0.1, logger=None) -> int
fetch_http_page(url, cursor)
main(argv=None) -> int
```

`connection` is a `sqlite3.Connection`; `fetch_page(cursor)` returns a decoded page dictionary. The sync result is the number of unique records committed.

Each page has exactly the required keys `items` (a list) and `next_cursor` (null or a nonempty string). Each item is a dictionary with nonempty string `id`, `name`, and timezone-aware ISO 8601 `updated_at`. Preserve timestamp text. Extra keys are ignored. Duplicate IDs use the last occurrence in fetch order.

Fetch from cursor `None`, then follow `next_cursor` until null. A repeated cursor or invalid page/record raises `ValueError`. Validate every page before changing the database. Upsert into `records(id TEXT PRIMARY KEY, name TEXT NOT NULL, updated_at TEXT NOT NULL)` in one transaction. A failed fetch, validation or database operation leaves the database unchanged, including when the table did not previously exist. Reject an already-active transaction before fetching; do not commit or roll back a transaction owned by the caller.

Define `TransientError`. Retry each page at most `max_attempts` total calls, sleeping `base_delay * 2**(attempt-1)` after non-final transient failures. Other exceptions propagate. Validate `max_attempts` as a positive integer excluding booleans and `base_delay` as a nonnegative finite number before fetching. Log completion without credentials or record contents.

`fetch_http_page` uses `urllib.request` for GET, appends a URL-encoded `cursor` query only when non-null while preserving existing query parameters, decodes UTF-8 JSON, and classifies HTTP 429, HTTP 5xx and `URLError` as `TransientError`.

The CLI accepts `--url`, `--db`, `--max-attempts`, `--base-delay`, with `SYNC_URL`, `SYNC_DB`, `SYNC_MAX_ATTEMPTS`, and `SYNC_BASE_DELAY` environment fallbacks. URL and database are required. Return 0 on success, 2 for invalid configuration and 1 for sync failure. Write safe diagnostics to stderr; never print the URL.

## Historical interpretation

The older v1 public and final checks did not prove the real CLI-to-fetch path. A later review found that a passing candidate could call the HTTP fetcher with the wrong signature, and a permissive mock concealed that defect. The v2 suite closes this gap. Keep v1 results labeled historical and do not present them as production-ready.
