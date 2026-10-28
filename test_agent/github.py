"""Small GitHub REST adapter with fixed hosts and bounded responses."""

import http.client
import json
import re

from test_agent.context import AgentError
from test_agent.pr_context import REPOSITORY, PRIdentity

MARKER = "<!-- tf-test-agent-report -->"


class GitHub:
    def __init__(self, repository, token):
        if not re.fullmatch(REPOSITORY, repository) or not token:
            raise AgentError("A repository identity and GitHub token are required.")
        self.repository, self._token = repository, token

    def request(self, method, suffix, body=None):
        connection = http.client.HTTPSConnection("api.github.com", timeout=30)
        try:
            connection.request(
                method,
                f"/repos/{self.repository}/{suffix}",
                body=None if body is None else json.dumps(body).encode(),
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "User-Agent": "tf-test-agent",
                    "Accept": "application/vnd.github+json",
                    "Content-Type": "application/json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            response = connection.getresponse()
            if response.status not in (200, 201):
                raise AgentError(f"GitHub returned HTTP {response.status}.")
            data = response.read(1_048_577)
            if len(data) > 1_048_576:
                raise AgentError("GitHub response exceeds its size limit.")
            return json.loads(data)
        except (OSError, http.client.HTTPException):
            raise AgentError("Network or timeout error contacting GitHub.") from None
        except ValueError:
            raise AgentError("GitHub returned invalid JSON.") from None
        finally:
            connection.close()

    def pull_request(self, number):
        if type(number) is not int or number < 1:
            raise AgentError("Invalid PR number.")
        data = self.request("GET", f"pulls/{number}")
        identity = PRIdentity(
            repository=self.repository,
            number=number,
            base_sha=data["base"]["sha"],
            head_sha=data["head"]["sha"],
        )
        reasons = []
        if data["state"] != "open":
            reasons.append("The pull request is closed.")
        if (
            data["base"]["ref"] != "main"
            or data["base"]["repo"]["full_name"] != self.repository
        ):
            reasons.append(
                "Only PRs targeting this repository's main branch are supported."
            )
        if (
            data["head"]["repo"] is None
            or data["head"]["repo"]["full_name"] != self.repository
        ):
            reasons.append("Live LLM runs on fork PRs are not enabled.")
        if data.get("draft"):
            reasons.append("Draft PRs do not invoke the model.")
        if data["user"]["type"] == "Bot":
            reasons.append("Bot PRs do not invoke the model.")
        return identity, reasons

    def publish(self, identity, body):
        current, reasons = self.pull_request(identity.number)
        if (
            identity.origin != "github"
            or current != identity
            or "The pull request is closed." in reasons
        ):
            return False
        comments = []
        for page in range(1, 11):
            batch = self.request(
                "GET", f"issues/{identity.number}/comments?per_page=100&page={page}"
            )
            comments.extend(batch)
            if len(batch) < 100:
                break
        else:
            raise AgentError(
                "Too many PR comments to identify the agent report safely."
            )
        owned = [
            item
            for item in comments
            if item["user"]["login"] == "github-actions[bot]"
            and item["user"]["type"] == "Bot"
            and item.get("body", "").startswith(MARKER)
        ]
        fresh, reasons = self.pull_request(identity.number)
        if fresh != identity or "The pull request is closed." in reasons:
            return False
        if owned:
            self.request("PATCH", f"issues/comments/{owned[-1]['id']}", {"body": body})
        else:
            self.request("POST", f"issues/{identity.number}/comments", {"body": body})
        return True
