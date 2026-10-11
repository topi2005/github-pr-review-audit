# Architecture contract

Read this file and `base.py` before changing anything. It describes what already exists and the conventions every integration must follow.

## What the project does

For one source system, list the items that reached a "done" state (GitHub: merged pull requests) and report, per item, whether it passed **review** (`CR_PASSED`) and whether all **required checks** passed (`CHECKS_PASSED`). Output is a CSV report plus the intermediate JSON of every step.

## Layout

```
base.py            shared layer (do not fork it per integration)
extract.py         GitHub extractor (name kept for assignment compliance)
transform.py       GitHub transformer + pure evaluation functions
main.py            CLI + INTEGRATIONS registry
utils.py           logging and JSON helpers
tests/             pytest, fully mocked, no network
ai_utils/          markdown resources for AI tools (this folder)
data/extracted/<integration>/     ids.json, details.json   (gitignored)
data/transformed/<integration>/   rows.json                (gitignored)
data/reports/                     <report_name>.csv        (committed)
```

New integrations add `<name>_extract.py` and `<name>_transform.py` at the repo root.

## Shared layer API (`base.py`)

- `ApiClient(headers=None, auth=None, retries=4, timeout=30)`
  - `.request(method, url, **kwargs) -> requests.Response`
  - Handles: 401 -> `PermissionError`; 502/503/504 -> exponential backoff; 429 or rate-limited 403 -> waits (`Retry-After` / `X-RateLimit-Reset`); any other 4xx/5xx -> `HTTPError`.
  - `headers={"Authorization": "Bearer ..."}` for token APIs, `auth=(user, secret)` for HTTP basic.
- `BaseExtractor(max_items=200, output_root="data")` (abstract)
  - Class attribute `name` selects the output folder.
  - Implement `fetch_item_ids() -> list` (at most `self.max_items`) and `fetch_item_detail(item_id) -> dict`.
  - Inherited: `run()` (lists, fetches, writes `ids.json` and `details.json`), `load_cached()`.
- `BaseTransformer` (abstract)
  - Implement `transform_item(raw) -> dict` returning one flat row.
  - Optional: `sort_field`, `sort_reverse`. Inherited: `transform_all(items)` (skips empty items, sorts).
- `Pipeline(extractor, transformer, report_name).run(skip_extract=False)`
  - extract (or load cache) -> transform -> `rows.json` -> CSV.

## Conventions

1. **Extractors only talk to the network. Transformers never do.** Transformers are pure functions over saved JSON so they can be tested and re-run with `--skip-extract`.
2. **Unknown is `None`, not `False`.** If a verdict cannot be determined from the data (no check data, nothing required), emit `None` (blank in CSV). Never claim a pass that was not verified.
3. **Every verdict has supporting columns** so a human can audit it (who approved, which checks failed).
4. **Log a warning when data is truncated** (a page cap was hit); never fail silently.
5. **Tolerate missing people.** Deleted or anonymized users appear as `null`; report them as a placeholder, do not crash.
6. **No secrets in code or git.** Configuration comes from `.env` and is documented in `.env.example`. Never print tokens.
7. **Tests are mocked.** No test may use the network or real credentials.
8. **Python 3.10+, type hints, small functions, docstrings only where the logic is non-obvious.**
9. **Keep `base.py` backward compatible.** If it must change, every existing test must still pass.

## Reference implementation (GitHub)

| Concept            | GitHub                                           |
|--------------------|--------------------------------------------------|
| Item               | Merged pull request                              |
| Auth               | Personal access token, `Authorization: Bearer`   |
| Listing            | GraphQL `pullRequests(states: MERGED)`, cursor paging |
| Detail             | GraphQL per PR: reviews + head-commit check contexts |
| `CR_PASSED`        | Latest decisive review per reviewer, excluding the author and post-merge reviews; true if at least one approval |
| `CHECKS_PASSED`    | All required checks on the last commit pass; `None` if unknown |
| Report columns     | `pr_number, pr_title, author, merge_date, CR_PASSED, CHECKS_PASSED, approved_by, changes_requested_by, required_checks, failed_checks` |

Match this level of care (edge cases, supporting columns, tests) in every new integration.
