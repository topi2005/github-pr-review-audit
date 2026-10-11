import os
import time

import requests
from dotenv import load_dotenv

from utils import get_logger, save_json

log = get_logger("extract")

GRAPHQL_URL = "https://api.github.com/graphql"

LIST_QUERY = """
query($owner: String!, $name: String!, $cursor: String, $pageSize: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: MERGED, first: $pageSize, after: $cursor,
                 orderBy: {field: UPDATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes { number }
    }
  }
}
"""

DETAIL_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      number
      title
      url
      mergedAt
      author { login }
      mergeCommit { oid }
      reviews(first: 100) {
        pageInfo { hasNextPage }
        nodes { state submittedAt author { login } }
      }
      commits(last: 1) {
        nodes {
          commit {
            oid
            statusCheckRollup {
              state
              contexts(first: 100) {
                pageInfo { hasNextPage }
                nodes {
                  __typename
                  ... on CheckRun {
                    name status conclusion
                    isRequired(pullRequestNumber: $number)
                  }
                  ... on StatusContext {
                    context state
                    isRequired(pullRequestNumber: $number)
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""


class GitHubExtractor:
    def __init__(self, token: str, owner: str, repo: str, max_prs: int = 200):
        if not token:
            raise ValueError("GITHUB_TOKEN is missing. Set it in your .env file.")
        self.owner, self.repo, self.max_prs = owner, repo, max_prs
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})

    def _post(self, query: str, variables: dict, retries: int = 4) -> dict:
        for attempt in range(1, retries + 1):
            resp = self.session.post(
                GRAPHQL_URL, json={"query": query, "variables": variables}, timeout=30
            )

            if resp.status_code == 401:
                raise PermissionError("Bad credentials: check your GITHUB_TOKEN.")

            # Transient server errors: exponential backoff
            if resp.status_code in (502, 503, 504):
                time.sleep(2 ** attempt)
                continue

            # Secondary rate limit / primary rate limit exhausted
            if resp.status_code in (403, 429):
                wait = int(resp.headers.get("Retry-After", 0))
                if not wait and resp.headers.get("X-RateLimit-Remaining") == "0":
                    wait = max(int(resp.headers["X-RateLimit-Reset"]) - int(time.time()), 1)
                log.warning("Rate limited, sleeping %ss", wait or 60)
                time.sleep(wait or 60)
                continue

            resp.raise_for_status()
            payload = resp.json()
            if payload.get("errors") and not payload.get("data"):
                raise RuntimeError(f"GraphQL errors: {payload['errors']}")
            return payload["data"]

        raise RuntimeError("GitHub request failed after retries.")

    def fetch_merged_pr_numbers(self) -> list[int]:
        numbers, cursor = [], None
        while len(numbers) < self.max_prs:
            page_size = min(100, self.max_prs - len(numbers))
            data = self._post(LIST_QUERY, {
                "owner": self.owner, "name": self.repo,
                "cursor": cursor, "pageSize": page_size,
            })
            prs = data["repository"]["pullRequests"]
            numbers += [n["number"] for n in prs["nodes"]]
            if not prs["pageInfo"]["hasNextPage"]:
                break
            cursor = prs["pageInfo"]["endCursor"]
        log.info("Found %d merged PRs", len(numbers))
        return numbers

    def fetch_pr_detail(self, number: int) -> dict:
        data = self._post(DETAIL_QUERY, {
            "owner": self.owner, "name": self.repo, "number": number,
        })
        return data["repository"]["pullRequest"]

    def run(self, output_dir: str = "data/extracted") -> list[dict]:
        numbers = self.fetch_merged_pr_numbers()
        save_json(numbers, f"{output_dir}/pr_numbers.json")

        details = []
        for i, n in enumerate(numbers, 1):
            details.append(self.fetch_pr_detail(n))
            if i % 25 == 0:
                log.info("Fetched %d/%d PRs", i, len(numbers))

        save_json(details, f"{output_dir}/pr_details.json")
        return details


if __name__ == "__main__":
    load_dotenv()
    GitHubExtractor(
        token=os.getenv("GITHUB_TOKEN"),
        owner=os.getenv("GITHUB_ORG", "home-assistant"),
        repo=os.getenv("GITHUB_REPO", "home-assistant.io"),
        max_prs=int(os.getenv("MAX_PRS", 200)),
    ).run()