# GitHub PR Review Audit

Fetches merged pull requests from a GitHub repository and reports, for each one, whether it was **approved by at least one reviewer** and whether **all required status checks passed** before merge.

Default target: [`home-assistant/home-assistant.io`](https://github.com/home-assistant/home-assistant.io). The repository is configurable, so the same code runs against any repo under the organization.

## Project structure

```
.
├── ai_utils/              # AI tool resources (markdown prompts and contracts)
├── data/
│   ├── extracted/<name>/  # raw source data (JSON), regenerated on each run
│   ├── transformed/<name>/# processed per-item results (JSON), regenerated
│   └── reports/           # final CSV report (committed)
├── tests/
│   ├── test_transform.py  # unit tests for review/check logic
│   └── test_base.py       # tests for the shared layer
├── base.py                # shared layer: ApiClient, BaseExtractor, BaseTransformer, Pipeline
├── extract.py             # GitHub extractor (GraphQL)
├── transform.py           # GitHub transformer: CR_PASSED and CHECKS_PASSED
├── main.py                # CLI and integration registry
├── utils.py               # logging and JSON helpers
├── pytest.ini
├── requirements.txt
└── .env.example
```

## Setup

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate         # macOS / Linux
source .venv/Scripts/activate     # Windows, Git Bash
.venv\Scripts\activate            # Windows, cmd / PowerShell
pip install -r requirements.txt
cp .env.example .env             # then edit .env
```

Configure `.env`:

| Variable       | Description                             | Default             |
| -------------- | --------------------------------------- | ------------------- |
| `GITHUB_TOKEN` | Personal access token (required)        | none                |
| `GITHUB_ORG`   | Organization / owner                    | `home-assistant`    |
| `GITHUB_REPO`  | Repository name                         | `home-assistant.io` |
| `MAX_PRS`      | Maximum number of merged PRs to process | `200`               |

**Token:** a classic PAT with no scopes, or a fine-grained token with read-only access to public repositories, is enough for public repos. Never commit `.env`.

## Usage

```bash
python main.py                  # extract from GitHub, transform, write report
python main.py --skip-extract   # reuse data/extracted/github/details.json (no API calls)
python main.py --integration github   # explicit; "github" is the default
python -m pytest -v             # run the tests
```

Each step writes its output to its own folder:

| Step      | Output                                           |
| --------- | ------------------------------------------------ |
| Extract   | `data/extracted/github/ids.json`, `details.json` |
| Transform | `data/transformed/github/rows.json`              |
| Report    | `data/reports/pr_review_report.csv`              |

## Report columns

| Column                 | Meaning                                                               |
| ---------------------- | --------------------------------------------------------------------- |
| `pr_number`            | Pull request number                                                   |
| `pr_title`             | Title                                                                 |
| `author`               | Author login (`ghost` if the account was deleted)                     |
| `merge_date`           | Merge timestamp (UTC, ISO 8601)                                       |
| `CR_PASSED`            | `True` if the PR passed code review (rules below)                     |
| `CHECKS_PASSED`        | `True` / `False`, or blank when it cannot be determined (rules below) |
| `approved_by`          | Reviewers whose final decision was approval (`;`-separated)           |
| `changes_requested_by` | Reviewers whose final decision was "changes requested"                |
| `required_checks`      | Names of the required checks that were evaluated                      |
| `failed_checks`        | Required checks that did not pass                                     |

The last four columns are supporting detail so each verdict can be audited.

## How the verdicts are computed

### CR_PASSED

1. Consider only reviews submitted at or before the merge time.
2. Ignore reviews by the PR's author (no self-approval).
3. For each reviewer, keep their **latest decisive review**: `APPROVED`, `CHANGES_REQUESTED`, or `DISMISSED`. Plain `COMMENTED` reviews never count for or against.
4. `CR_PASSED` is `True` if at least one reviewer's final state is `APPROVED`.

Consequences: a reviewer who requested changes and later approved counts as an approval; a reviewer who approved and later requested changes does not.

### CHECKS_PASSED

1. Take the check runs and commit statuses on the PR's **last commit before merge**.
2. Keep only those GitHub marks as **required** for this PR (`isRequired`).
3. A check run passes if it is `COMPLETED` with conclusion `SUCCESS`, `NEUTRAL`, or `SKIPPED`. A commit status passes if its state is `SUCCESS`.
4. If several runs share a name (e.g. the same job in two workflows), **all** must pass.
5. `True` if every required check passed, `False` if any did not.
6. **Blank (unknown)** if there is no check data at all, or no check is marked required. This is deliberate: reporting "passed" when nothing was verified would be misleading.

## Design decisions

- **GraphQL instead of REST.** The REST approach needs several calls per PR (PR, reviews, check runs, statuses). GraphQL returns reviews and check results for a PR in a single request.
- **Two-phase extraction.** Phase 1 lists merged PR numbers (paginated). Phase 2 fetches details per PR. Phase 2 is separate because `isRequired(pullRequestNumber: N)` needs the PR number as a query variable, which can't be taken from a parent field inside one query.
- **Required checks without admin access.** The branch-protection REST endpoint needs admin rights on the repository. `isRequired` on check contexts exposes the same information to any authenticated user.
- **Resilience.** Retries with exponential backoff on 502/503/504, waits on rate limits (`Retry-After` / `X-RateLimit-Reset`), a clear error for bad credentials, and tolerance for deleted authors/reviewers and null check rollups.
- **Separation of concerns.** `extract.py` only talks to GitHub, `transform.py` is pure functions over saved JSON (easy to test, no network), and `main.py` only wires them together. `--skip-extract` lets you iterate on the logic without re-fetching.
- **Shared layer for multiple integrations.** `base.py` holds everything that is not GitHub-specific: `ApiClient` (auth, retries, rate limits), `BaseExtractor` and `BaseTransformer` (abstract classes), and `Pipeline` (extract, transform, CSV). A new integration implements two small classes and registers itself in `main.py`.

## Known limitations

- **Required checks reflect today's branch protection rules**, not the rules at merge time. GitHub does not expose historical rules, so a check that became required (or stopped being required) after a PR merged is evaluated under the current rules.
- **Only the last commit before merge is inspected.** Checks that ran only on the merge commit itself are not included.
- **Reviews and check contexts are capped at 100 per PR.** A warning is logged if a PR exceeds this; results for such PRs may be partial. Pagination of these inner lists is not implemented.
- **"Most recent" means most recently updated.** Merged PRs are listed ordered by last-updated time, because the GraphQL API cannot order by merge time. With `MAX_PRS` set, the sample is the most recently _updated_ merged PRs, not strictly the most recently merged.
- **Bot approvals count as approvals.** A review by a bot account with state `APPROVED` would be treated like any other. In the observed data, review bots only leave `COMMENTED` reviews.
- **"At least one approval" is the rule.** A PR with one approval and another reviewer's outstanding "changes requested" is reported `CR_PASSED = True`, with the blocker listed in `changes_requested_by`. This follows the assignment wording; GitHub's own merge gate would be stricter.

## Testing

- `tests/test_transform.py` (24 tests) covers the review and check verdict logic using hand-built PR payloads: review ordering, approve-then-request-changes (and the reverse), dismissed and comment-only reviews, self-approval, post-merge reviews, duplicate check names, failed/in-progress/non-required checks, null rollups, deleted authors, and pagination warnings.
- `tests/test_extract.py` (13 tests) covers `GitHubExtractor`: cursor pagination, the `max_items` cap, request payloads, GraphQL error handling, and a full `run()` writing its JSON outputs.
- `tests/test_base.py` (8 tests) covers the shared layer: a fake integration run through `Pipeline`, plus `ApiClient` retry, rate-limit and auth behaviour.

## Extending to other integrations

Every integration follows the same shape:

1. Subclass `BaseExtractor`: implement `fetch_item_ids()` and `fetch_item_detail()`.
2. Subclass `BaseTransformer`: implement `transform_item()` to produce one flat report row.
3. Register a builder in `INTEGRATIONS` in `main.py`, then run `python main.py --integration <name>`.

Authentication, retries, rate limiting, JSON outputs and the CSV report come from `base.py`.

### AI-assisted integrations

`ai_utils/` contains the resources for adding an integration with an AI coding tool:

- `architecture_contract.md`: the shared API, conventions and a GitHub reference implementation.
- `jira_integration_prompt.md`: a ready-to-paste prompt that reproduces the whole project for **Jira Cloud** (API-token auth, fetching resolved tickets, review and sub-task verdicts, CSV report, tests and docs) on top of the shared layer.

The Jira integration is specified by that prompt rather than implemented in this repository. The prompt documents the Jira-specific API behaviour an implementation has to respect (the `/rest/api/3/search/jql` endpoint, `nextPageToken` pagination, explicit `fields`) and defines how "passed review" and "checks passed" map onto Jira concepts.
