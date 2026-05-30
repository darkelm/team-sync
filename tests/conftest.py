"""Shared pytest fixtures for SyncBot test suite.

Key fixtures:
- providers: local Providers over the synthetic org (no API keys needed)
- tmp_state: monkeypatches all module-level state-file paths to a tmp dir
- tmp_config: writes a minimal config.yaml pointing to the synthetic data
"""
from __future__ import annotations

import os
import yaml
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="session")
def config_path() -> str:
    return os.path.join(REPO_ROOT, "config.yaml")


@pytest.fixture(scope="session")
def providers(config_path):
    from src.providers.factory import Providers
    return Providers(config_path)


@pytest.fixture()
def tmp_state(tmp_path, monkeypatch):
    """Redirect every module-level state-file path to tmp_path so tests never
    pollute the real data/ directory."""
    snap = str(tmp_path / "health_snapshots.json")
    notif = str(tmp_path / "notification_prefs.json")
    aud = str(tmp_path / "audience_prefs.json")

    import src.agent.health as health_mod
    import src.agent.preferences as prefs_mod
    import src.agent.audience as audience_mod

    monkeypatch.setattr(health_mod, "SNAPSHOT_PATH", snap)
    monkeypatch.setattr(prefs_mod, "DEFAULT_PATH", notif, raising=False)
    monkeypatch.setattr(audience_mod, "STORE_PATH", aud)

    yield tmp_path


@pytest.fixture()
def tmp_config(tmp_path) -> str:
    """Write a minimal config.yaml that points at synthetic data and a tmp teams dir."""
    synthetic_path = os.path.join(REPO_ROOT, "data", "synthetic")
    teams_dir = os.path.join(REPO_ROOT, "data", "synthetic", "teams")
    cfg = {
        "providers": {
            "jira": "local", "confluence": "local", "github": "local",
            "slack": "local", "figma": "local",
        },
        "data": {
            "synthetic_path": synthetic_path,
            "teams_dir": teams_dir,
        },
        "leadership": {
            "unit_label": "team",
            "portfolio_label": "portfolio",
            "exec_channel": "",
        },
    }
    cfg_file = str(tmp_path / "config.yaml")
    with open(cfg_file, "w") as f:
        yaml.dump(cfg, f)
    return cfg_file


@pytest.fixture()
def tmp_ingest_config(tmp_path) -> str:
    """Config pointing to a fresh teams dir under tmp_path for ingest tests."""
    teams_dir = str(tmp_path / "teams")
    os.makedirs(teams_dir, exist_ok=True)
    cfg = {
        "providers": {
            "jira": "local", "confluence": "local", "github": "local",
            "slack": "local", "figma": "local",
        },
        "data": {
            "synthetic_path": str(tmp_path),
            "teams_dir": teams_dir,
        },
    }
    cfg_file = str(tmp_path / "config.yaml")
    with open(cfg_file, "w") as f:
        yaml.dump(cfg, f)
    return cfg_file
