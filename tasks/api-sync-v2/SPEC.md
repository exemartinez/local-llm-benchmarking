# API-to-SQLite synchronization task — v2

Implement `candidate.py` with:

```python
sync(connection, fetch_page, sleep, *, max_attempts=3, base_delay=0.1, logger=None) -> int
fetch_http_page(url, cursor)
main(argv=None) -> int
```

`sync` must fetch every page before any persistent database change; validate cursor and record shapes; reject repeated cursors, invalid retry settings and a caller-owned active transaction; resolve duplicate record IDs by last occurrence; and atomically upsert all validated records. A failed fetch, validation or database write must leave database state unchanged, including removal of a newly-created table. Retry only `TransientError` per page using exponential backoff. Log successful completion without URLs, credentials or record contents.

Pages contain an `items` list and `next_cursor` (null or non-empty string). Items require non-empty string `id`, `name`, and timezone-aware ISO-8601 `updated_at`; extra fields are ignored. The database table is `records(id TEXT PRIMARY KEY, name TEXT NOT NULL, updated_at TEXT NOT NULL)`.

`fetch_http_page` uses `urllib.request.Request` and `urlopen` to issue GET, preserving existing URL query keys and encoding a non-null cursor. It decodes a UTF-8 JSON response. HTTP 429, HTTP 5xx and `URLError` become `TransientError`; other HTTP errors propagate. Keep the transport callable as a replaceable module-level `urlopen` reference so tests can replace only the network transport.

`main(argv=None)` parses `--url`, `--db`, `--max-attempts`, `--base-delay`; environment variables `SYNC_URL`, `SYNC_DB`, `SYNC_MAX_ATTEMPTS`, `SYNC_BASE_DELAY` are fallbacks. CLI values win. URL and database are required. It opens SQLite, connects the actual `fetch_http_page` into `sync`, emits a safe completion message, and closes its database connection on success and failure. Return 0 on success, 2 for invalid configuration and 1 for sync failure. Never log the URL or returned data.

## v2 integration requirements

- Do not replace or mock `main`, `sync`, or `fetch_http_page` in tests.
- The end-to-end CLI test must invoke `main(argv)` and replace only `candidate.urlopen`, the network transport.
- Check the outgoing request method and URL-encoded cursor while preserving pre-existing query parameters.
- Check response closure, SQLite connection closure, invalid configuration before network activity, safe successful logging and rollback after a later-page transport failure.
- Public tests and their output may be shown to models during bounded repairs. Final checks stay separate and their output must never be sent back to a model.

## Execution warning

Generated code is untrusted. The benchmark runner executes a candidate as the current Windows account only after an explicit operator gate. The Python process, venv, pytest and mocked transport are not a sandbox. Review the saved candidate before accepting execution; use a separately configured Windows Sandbox or disposable VM if isolation is required. Isolation is not created or implied by this project.
