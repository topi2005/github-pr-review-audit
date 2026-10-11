# Prompt: add a Jira integration

Paste everything below the line into your AI coding tool, run from the repository root.

---

You are working in an existing Python project that audits "done" work items from a source system and writes a CSV report. It currently supports GitHub (merged pull requests). Your task is to add a **Jira Cloud** integration that reproduces the same outputs for Jira tickets, reusing the shared layer.

## First, read

1. `ai_utils/architecture_contract.md` (conventions and shared API)
2. `base.py`, `extract.py`, `transform.py`, `main.py`, `tests/`

Do not start coding until you have read them. Follow the same patterns, naming and level of care as the GitHub integration.

## What to build

A Jira integration that, using an API token, fetches **resolved tickets from a single project** and reports per ticket whether it passed review and whether its required checks passed, writing the same folder structure of outputs as GitHub.

### Files

- `jira_extract.py`: `JiraExtractor(BaseExtractor)` with `name = "jira"`.
- `jira_transform.py`: `JiraTransformer(BaseTransformer)` plus pure evaluation functions.
- `main.py`: add a `build_jira()` builder and register it as `"jira"` in `INTEGRATIONS`. The GitHub behaviour must not change.
- `.env.example`: add the Jira variables below (no real values).
- `tests/test_jira_transform.py` and `tests/test_jira_extract.py`.
- `README.md`: fill in the "Extending to other integrations" section for Jira.

Do **not** duplicate retry, rate-limit, file-writing, CSV or pipeline code. Use `ApiClient`, `BaseExtractor`, `BaseTransformer` and `Pipeline`. Change `base.py` only if truly necessary, and keep all existing tests passing.

### Configuration (`.env`)

| Variable               | Meaning                                                  | Default                              |
|------------------------|----------------------------------------------------------|--------------------------------------|
| `JIRA_BASE_URL`        | e.g. `https://your-site.atlassian.net`                   | required                             |
| `JIRA_EMAIL`           | Atlassian account email                                  | required                             |
| `JIRA_API_TOKEN`       | API token                                                | required                             |
| `JIRA_PROJECT`         | Project key to audit                                     | required                             |
| `JIRA_REVIEW_STATUSES` | Comma-separated status names that count as review        | `In Review,Code Review,In QA`        |
| `MAX_TICKETS`          | Maximum tickets to process                               | `200`                                |

Fail fast with a clear message if a required variable is missing.

### Authentication

Jira Cloud uses HTTP basic auth with the account email and API token: `ApiClient(auth=(email, token))`. Send `Accept: application/json`.

### Jira API facts you must respect

- Use `GET /rest/api/3/search/jql`. The old `/rest/api/3/search` endpoint has been **retired**; do not use it.
- Pagination is token-based: follow `nextPageToken` until it is absent or `isLast` is true. There is no `startAt` and **no total count**.
- That endpoint returns **only issue ids unless you pass `fields`**. Always request the fields you need explicitly.
- Query: `project = "<KEY>" AND statusCategory = Done ORDER BY resolved DESC`. Quote the project key. Jira rejects invalid JQL, so surface error bodies clearly.
- For per-ticket detail use `GET /rest/api/3/issue/{key}` with `expand=changelog` and explicit `fields` (at least: `summary, status, reporter, assignee, created, resolutiondate, subtasks`).
- The embedded changelog can be truncated. If `changelog.total` exceeds the histories returned, page through `GET /rest/api/3/issue/{key}/changelog` until `isLast`.
- Sub-task entries do not include their resolution date. Fetch it separately (for example `POST /rest/api/3/issue/bulkfetch` with `fields: ["resolutiondate"]`, respecting its batch limit, or a JQL search on `parent = KEY`).
- Jira timestamps look like `2026-10-09T10:15:45.000+0200`. Parse them with `datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z")` and normalise to UTC.

### Mapping GitHub concepts to Jira

| GitHub                 | Jira (default definition)                                   |
|------------------------|-------------------------------------------------------------|
| Merged pull request    | Ticket in status category Done                              |
| Author                 | Reporter                                                    |
| Merge date             | `resolutiondate`                                            |
| `CR_PASSED`            | The ticket entered a status in `JIRA_REVIEW_STATUSES` (found in the changelog) **at or before** its resolution date |
| `CHECKS_PASSED`        | Every sub-task was resolved at or before the parent's resolution date. `None` if the ticket has no sub-tasks or a sub-task's resolution cannot be determined |

These definitions are assumptions. Implement them exactly as written, make the review statuses configurable, and **document the assumptions and limitations in the README**.

### Report

CSV at `data/reports/jira_ticket_report.csv`, one row per ticket, sorted newest resolution first, with columns:

`ticket_key, ticket_title, reporter, resolved_date, CR_PASSED, CHECKS_PASSED, reviewed_in_status, open_subtasks_at_resolution`

(`reviewed_in_status` is the review status that satisfied `CR_PASSED`; `open_subtasks_at_resolution` lists sub-task keys not yet resolved when the parent was.) Intermediate JSON goes to `data/extracted/jira/` and `data/transformed/jira/` via the base classes.

### Edge cases to handle

- Reporter or assignee is `null` (deleted or anonymised user): use `"unknown"`.
- No `resolutiondate` despite Done category: `CR_PASSED` and `CHECKS_PASSED` are `None`.
- Ticket never entered a review status: `CR_PASSED = False`.
- Status renamed over time: match status names case-insensitively and ignore surrounding whitespace.
- Changelog or sub-task lists truncated: page, or log a warning if you cannot.
- Rate limits and transient errors: already handled by `ApiClient`; do not reimplement.
- Zero matching tickets: write an empty report with headers, do not crash.

## Tests

All mocked, no network, no credentials. Cover at least: review before vs after resolution, case-insensitive status matching, never reviewed, resolved sub-tasks vs one resolved late, no sub-tasks (`None`), missing resolution date, null reporter, timestamp parsing with offsets, `nextPageToken` pagination across two pages (including that `fields` is requested), changelog pagination, and a full pipeline run through `Pipeline` with a fake client.

## Definition of done

1. `python -m pytest -q` passes, including every pre-existing test.
2. `python main.py --integration jira` runs end to end against a real site configured through `.env`; `python main.py` (GitHub) behaves exactly as before.
3. `base.py` is unchanged, or the change is minimal, backward compatible and explained.
4. No secrets in the repository; `.env.example` is updated.
5. The README documents setup, the Jira definitions above, assumptions and limitations.

When finished, reply with a short summary: files added or changed, any assumption you had to make beyond this prompt, and anything you could not verify without a real Jira site.
