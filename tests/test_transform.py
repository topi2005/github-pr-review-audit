import logging

from transform import evaluate_checks, evaluate_reviews, transform_all, transform_pr

MERGED_AT = "2026-10-09T10:00:00Z"


# ---------- builders ----------

def at(hour: int) -> str:
    return f"2026-10-09T{hour:02d}:00:00Z"


def review(user, state, ts=None):
    return {
        "state": state,
        "submittedAt": ts or at(8),
        "author": {"login": user} if user else None,
    }


def check_run(name, conclusion="SUCCESS", status="COMPLETED", required=True):
    return {"__typename": "CheckRun", "name": name, "status": status,
            "conclusion": conclusion, "isRequired": required}


def status_ctx(context, state="SUCCESS", required=True):
    return {"__typename": "StatusContext", "context": context,
            "state": state, "isRequired": required}


def make_pr(reviews=(), contexts=(), *, number=1, author="alice",
            merged_at=MERGED_AT, null_rollup=False,
            reviews_has_next=False, contexts_has_next=False):
    rollup = None if null_rollup else {
        "state": "SUCCESS",
        "contexts": {"pageInfo": {"hasNextPage": contexts_has_next},
                     "nodes": list(contexts)},
    }
    return {
        "number": number,
        "title": f"PR {number}",
        "url": f"https://example.com/pull/{number}",
        "mergedAt": merged_at,
        "author": {"login": author} if author else None,
        "mergeCommit": {"oid": "abc"},
        "reviews": {"pageInfo": {"hasNextPage": reviews_has_next},
                    "nodes": list(reviews)},
        "commits": {"nodes": [{"commit": {"oid": "def", "statusCheckRollup": rollup}}]},
    }


# ---------- code review ----------

def test_single_approval_passes():
    pr = make_pr(reviews=[review("bob", "APPROVED")])
    result = evaluate_reviews(pr)
    assert result["cr_passed"] is True
    assert result["approved_by"] == ["bob"]


def test_no_reviews_fails():
    assert evaluate_reviews(make_pr())["cr_passed"] is False


def test_changes_requested_then_approved_passes():
    # Mirrors PR #47453 in the sample data
    pr = make_pr(reviews=[
        review("bob", "CHANGES_REQUESTED", at(7)),
        review("bob", "APPROVED", at(8)),
    ])
    assert evaluate_reviews(pr)["cr_passed"] is True


def test_approved_then_changes_requested_fails():
    pr = make_pr(reviews=[
        review("bob", "APPROVED", at(8)),
        review("bob", "CHANGES_REQUESTED", at(9)),
    ])
    result = evaluate_reviews(pr)
    assert result["cr_passed"] is False
    assert result["changes_requested_by"] == ["bob"]


def test_review_order_in_payload_does_not_matter():
    pr = make_pr(reviews=[
        review("bob", "APPROVED", at(8)),
        review("bob", "CHANGES_REQUESTED", at(7)),  # earlier, listed later
    ])
    assert evaluate_reviews(pr)["cr_passed"] is True


def test_later_comment_does_not_override_approval():
    pr = make_pr(reviews=[
        review("bob", "APPROVED", at(8)),
        review("bob", "COMMENTED", at(9)),
    ])
    assert evaluate_reviews(pr)["cr_passed"] is True


def test_dismissed_approval_does_not_count():
    pr = make_pr(reviews=[
        review("bob", "APPROVED", at(8)),
        review("bob", "DISMISSED", at(9)),
    ])
    assert evaluate_reviews(pr)["cr_passed"] is False


def test_author_self_approval_ignored():
    pr = make_pr(author="alice", reviews=[review("alice", "APPROVED")])
    assert evaluate_reviews(pr)["cr_passed"] is False


def test_approval_after_merge_ignored():
    pr = make_pr(merged_at=at(10), reviews=[review("bob", "APPROVED", at(11))])
    assert evaluate_reviews(pr)["cr_passed"] is False


def test_reviewer_with_deleted_account_ignored():
    pr = make_pr(reviews=[review(None, "APPROVED")])
    assert evaluate_reviews(pr)["cr_passed"] is False


def test_one_approver_one_blocker_passes_but_blocker_reported():
    # Documents the rule: at least one approval is enough
    pr = make_pr(reviews=[
        review("bob", "APPROVED"),
        review("carol", "CHANGES_REQUESTED"),
    ])
    result = evaluate_reviews(pr)
    assert result["cr_passed"] is True
    assert result["changes_requested_by"] == ["carol"]


# ---------- required checks ----------

def test_all_required_checks_pass():
    pr = make_pr(contexts=[
        check_run("Lint Markdown"),
        status_ctx("netlify/deploy-preview"),
    ])
    result = evaluate_checks(pr)
    assert result["checks_passed"] is True
    assert result["failed_checks"] == []


def test_required_check_failure_fails_and_is_named():
    pr = make_pr(contexts=[
        check_run("Lint Markdown"),
        check_run("Lint Text", conclusion="FAILURE"),
    ])
    result = evaluate_checks(pr)
    assert result["checks_passed"] is False
    assert result["failed_checks"] == ["Lint Text"]


def test_non_required_failure_is_ignored():
    pr = make_pr(contexts=[
        check_run("Lint Markdown"),
        check_run("Build Astro site", conclusion="FAILURE", required=False),
    ])
    assert evaluate_checks(pr)["checks_passed"] is True


def test_duplicate_check_names_all_must_pass():
    # Mirrors the duplicated "Lint Markdown" runs in the sample data
    pr = make_pr(contexts=[
        check_run("Lint Markdown", conclusion="SUCCESS"),
        check_run("Lint Markdown", conclusion="FAILURE"),
    ])
    assert evaluate_checks(pr)["checks_passed"] is False


def test_neutral_and_skipped_count_as_passing():
    pr = make_pr(contexts=[
        check_run("A", conclusion="NEUTRAL"),
        check_run("B", conclusion="SKIPPED"),
    ])
    assert evaluate_checks(pr)["checks_passed"] is True


def test_in_progress_required_check_fails():
    pr = make_pr(contexts=[check_run("A", status="IN_PROGRESS", conclusion=None)])
    assert evaluate_checks(pr)["checks_passed"] is False


def test_required_status_context_failure_fails():
    pr = make_pr(contexts=[status_ctx("netlify/deploy-preview", state="FAILURE")])
    result = evaluate_checks(pr)
    assert result["checks_passed"] is False
    assert result["failed_checks"] == ["netlify/deploy-preview"]


def test_null_rollup_is_unknown():
    assert evaluate_checks(make_pr(null_rollup=True))["checks_passed"] is None


def test_no_required_checks_is_unknown():
    pr = make_pr(contexts=[check_run("Build", required=False)])
    assert evaluate_checks(pr)["checks_passed"] is None


# ---------- full row ----------

def test_deleted_author_becomes_ghost():
    row = transform_pr(make_pr(author=None))
    assert row["author"] == "ghost"


def test_row_has_expected_columns():
    row = transform_pr(make_pr(
        reviews=[review("bob", "APPROVED")],
        contexts=[check_run("Lint Markdown")],
    ))
    assert {"pr_number", "pr_title", "author", "merge_date",
            "CR_PASSED", "CHECKS_PASSED"} <= set(row)
    assert row["CR_PASSED"] is True and row["CHECKS_PASSED"] is True


def test_transform_all_sorted_newest_first():
    older = make_pr(number=1, merged_at=at(8))
    newer = make_pr(number=2, merged_at=at(9))
    rows = transform_all([older, newer])
    assert [r["pr_number"] for r in rows] == [2, 1]


def test_review_pagination_overflow_logs_warning(caplog):
    caplog.set_level(logging.WARNING)
    transform_pr(make_pr(reviews_has_next=True))
    assert "more than 100 reviews" in caplog.text