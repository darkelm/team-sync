"""Provider factory — reads config and returns the right implementation."""
import os
import yaml
from dotenv import load_dotenv
load_dotenv()
from .base import JiraProvider, ConfluenceProvider, GitHubProvider, FigmaProvider, SlackProvider, ManifestProvider
from .local.manifests import LocalManifestProvider
from .local.jira import LocalJiraProvider
from .local.confluence import LocalConfluenceProvider
from .local.github import LocalGitHubProvider
from .local.figma import LocalFigmaProvider
from .local.slack import LocalSlackProvider


def _mode(key: str, config: dict) -> str:
    # config.yaml `providers:` is the source of truth; a *_PROVIDER env var is an
    # optional per-process override (see .env.example).
    return os.getenv(f"{key.upper()}_PROVIDER") or config.get("providers", {}).get(key, "local")


def load_config(config_path: str = "config.yaml") -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


class Providers:
    def __init__(self, config_path: str = "config.yaml"):
        cfg = load_config(config_path)
        teams_dir = cfg.get("data", {}).get("teams_dir", "./data/synthetic/teams")
        self.teams_dir = teams_dir  # exposed so callers can scope file reads to this project

        self.manifests: ManifestProvider = LocalManifestProvider(teams_dir)
        self.jira: JiraProvider = self._make_jira(_mode("jira", cfg), teams_dir)
        self.confluence: ConfluenceProvider = self._make_confluence(_mode("confluence", cfg), teams_dir)
        self.github: GitHubProvider = self._make_github(_mode("github", cfg), teams_dir)
        self.figma: FigmaProvider = self._make_figma(_mode("figma", cfg), teams_dir)
        self.slack: SlackProvider = self._make_slack(_mode("slack", cfg))

    def _make_jira(self, mode: str, teams_dir: str) -> JiraProvider:
        if mode == "live":
            from .live.jira import LiveJiraProvider
            return LiveJiraProvider()
        return LocalJiraProvider(teams_dir)

    def _make_confluence(self, mode: str, teams_dir: str) -> ConfluenceProvider:
        if mode == "live":
            from .live.confluence import LiveConfluenceProvider
            return LiveConfluenceProvider()
        return LocalConfluenceProvider(teams_dir)

    def _make_github(self, mode: str, teams_dir: str) -> GitHubProvider:
        if mode == "live":
            from .live.github import LiveGitHubProvider
            return LiveGitHubProvider()
        return LocalGitHubProvider(teams_dir)

    def _make_figma(self, mode: str, teams_dir: str) -> FigmaProvider:
        if mode == "live":
            from .live.figma import LiveFigmaProvider
            return LiveFigmaProvider()
        return LocalFigmaProvider(teams_dir)

    def _make_slack(self, mode: str) -> SlackProvider:
        if mode == "live":
            from .live.slack import LiveSlackProvider
            return LiveSlackProvider()
        return LocalSlackProvider()
