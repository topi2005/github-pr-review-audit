from base import ApiClient, BaseExtractor
from utils import get_logger

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


class GitHubExtractor(BaseExtractor):
    name = "github"

    def __init__(self, token: str, owner: str, repo: str,
                 max_items: int = 200, output_root: str = "data"):
        if not token:
            raise ValueError("GITHUB_TOKEN is missing. Set it in your .env file.")
        super().__init__(max_items=max_items, output_root=output_root)
        self.owner, self.repo = owner, repo
        self.client = ApiClient(headers={"Authorization": f"Bearer {token}"})

    def _graphql(self, query: str, variables: dict) -> dict:
        resp = self.client.request(
            "POST", GRAPHQL_URL, json={"query": query, "variables": variables}
        )
        payload = resp.json()
        if payload.get("errors") and not payload.get("data"):
            raise RuntimeError(f"GraphQL errors: {payload['errors']}")
        return payload["data"]

    def fetch_item_ids(self) -> list[int]:
        numbers, cursor = [], None
        while len(numbers) < self.max_items:
            page_size = min(100, self.max_items - len(numbers))
            data = self._graphql(LIST_QUERY, {
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

    def fetch_item_detail(self, number: int) -> dict:
        data = self._graphql(DETAIL_QUERY, {
            "owner": self.owner, "name": self.repo, "number": number,
        })
        return data["repository"]["pullRequest"]