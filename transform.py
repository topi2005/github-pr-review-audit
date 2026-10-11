from datetime import datetime

from utils import get_logger, load_json, save_json

log = get_logger("transform")

DECISIVE_REVIEW_STATES = {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}
PASSING_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}


def _parse_ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def evaluate_reviews(pr: dict) -> dict:
    """Latest decisive review per reviewer, counted only up to merge time."""
    merged_at = _parse_ts(pr["mergedAt"])
    pr_author = (pr.get("author") or {}).get("login")

    reviews = sorted(
        pr["reviews"]["nodes"], key=lambda r: r.get("submittedAt") or ""
    )
    latest: dict[str, str] = {}
    for review in reviews:
        reviewer = (review.get("author") or {}).get("login")
        submitted = _parse_ts(review.get("submittedAt"))
        if not reviewer or reviewer == pr_author:
            continue
        if review["state"] not in DECISIVE_REVIEW_STATES:
            continue
        if submitted and merged_at and submitted > merged_at:
            continue
        latest[reviewer] = review["state"]

    approvers = sorted(u for u, s in latest.items() if s == "APPROVED")
    blockers = sorted(u for u, s in latest.items() if s == "CHANGES_REQUESTED")
    return {
        "cr_passed": len(approvers) > 0,
        "approved_by": approvers,
        "changes_requested_by": blockers,
    }


def _context_passed(ctx: dict) -> tuple[str, bool] | None:
    kind = ctx.get("__typename")
    if kind == "CheckRun":
        ok = ctx["status"] == "COMPLETED" and ctx["conclusion"] in PASSING_CONCLUSIONS
        return ctx["name"], ok
    if kind == "StatusContext":
        return ctx["context"], ctx["state"] == "SUCCESS"
    return None


def evaluate_checks(pr: dict) -> dict:
    """Did every *required* check on the head commit pass? None = unknown."""
    commits = pr["commits"]["nodes"]
    rollup = commits[0]["commit"]["statusCheckRollup"] if commits else None
    if not rollup:
        return {"checks_passed": None, "required_checks": [], "failed_checks": []}

    if rollup["contexts"]["pageInfo"]["hasNextPage"]:
        log.warning("PR #%s has more than 100 check contexts; results may be partial", pr["number"])

    results: dict[str, list[bool]] = {}
    for ctx in rollup["contexts"]["nodes"]:
        if not ctx.get("isRequired"):
            continue
        parsed = _context_passed(ctx)
        if parsed:
            name, ok = parsed
            results.setdefault(name, []).append(ok)

    if not results:
        return {"checks_passed": None, "required_checks": [], "failed_checks": []}

    failed = sorted(name for name, oks in results.items() if not all(oks))
    return {
        "checks_passed": not failed,
        "required_checks": sorted(results),
        "failed_checks": failed,
    }


def transform_pr(pr: dict) -> dict:
    if pr["reviews"]["pageInfo"]["hasNextPage"]:
        log.warning("PR #%s has more than 100 reviews; results may be partial", pr["number"])

    reviews = evaluate_reviews(pr)
    checks = evaluate_checks(pr)
    return {
        "pr_number": pr["number"],
        "pr_title": pr["title"],
        "author": (pr.get("author") or {}).get("login", "ghost"),  # deleted accounts
        "merge_date": pr["mergedAt"],
        "CR_PASSED": reviews["cr_passed"],
        "CHECKS_PASSED": checks["checks_passed"],
        "approved_by": ";".join(reviews["approved_by"]),
        "changes_requested_by": ";".join(reviews["changes_requested_by"]),
        "required_checks": ";".join(checks["required_checks"]),
        "failed_checks": ";".join(checks["failed_checks"]),
    }


def transform_all(details: list[dict]) -> list[dict]:
    rows = [transform_pr(pr) for pr in details if pr]  # skip null PRs
    return sorted(rows, key=lambda r: r["merge_date"], reverse=True)