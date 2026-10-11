import argparse
import os

from dotenv import load_dotenv

from base import Pipeline
from extract import GitHubExtractor
from transform import GitHubTransformer
from utils import get_logger

log = get_logger("main")


def build_github() -> Pipeline:
    extractor = GitHubExtractor(
        token=os.getenv("GITHUB_TOKEN"),
        owner=os.getenv("GITHUB_ORG", "home-assistant"),
        repo=os.getenv("GITHUB_REPO", "home-assistant.io"),
        max_items=int(os.getenv("MAX_PRS", 200)),
    )
    return Pipeline(extractor, GitHubTransformer(), report_name="pr_review_report.csv")


# Register new integrations here, e.g. "jira": build_jira
INTEGRATIONS = {"github": build_github}


def main() -> None:
    parser = argparse.ArgumentParser(description="Integration data audit pipeline")
    parser.add_argument("--integration", choices=sorted(INTEGRATIONS), default="github")
    parser.add_argument("--skip-extract", action="store_true",
                        help="reuse previously extracted data instead of calling the API")
    args = parser.parse_args()

    load_dotenv()
    INTEGRATIONS[args.integration]().run(skip_extract=args.skip_extract)


if __name__ == "__main__":
    main()