import argparse
import os

import pandas as pd
from dotenv import load_dotenv

from extract import GitHubExtractor
from transform import transform_all
from utils import get_logger, load_json, save_json

log = get_logger("main")


def main(skip_extract: bool = False) -> None:
    load_dotenv()

    if skip_extract:
        details = load_json("data/extracted/pr_details.json")
        log.info("Loaded %d PRs from existing extract", len(details))
    else:
        details = GitHubExtractor(
            token=os.getenv("GITHUB_TOKEN"),
            owner=os.getenv("GITHUB_ORG", "home-assistant"),
            repo=os.getenv("GITHUB_REPO", "home-assistant.io"),
            max_prs=int(os.getenv("MAX_PRS", 200)),
        ).run()

    rows = transform_all(details)
    save_json(rows, "data/transformed/pr_report.json")

    os.makedirs("data/reports", exist_ok=True)
    pd.DataFrame(rows).to_csv("data/reports/pr_review_report.csv", index=False)
    log.info("Report written: %d PRs", len(rows))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Merged PR review/checks audit")
    parser.add_argument("--skip-extract", action="store_true",
                        help="reuse data/extracted/pr_details.json instead of calling GitHub")
    main(parser.parse_args().skip_extract)