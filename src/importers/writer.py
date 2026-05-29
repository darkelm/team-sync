"""Write normalized imports into the per-team JSON the local providers read."""
import json
import os
from pydantic import BaseModel


def write_team_json(items: list[BaseModel], teams_dir: str, team_slug: str, filename: str) -> str:
    team_dir = os.path.join(teams_dir, team_slug)
    os.makedirs(team_dir, exist_ok=True)
    path = os.path.join(team_dir, filename)
    payload = [json.loads(i.model_dump_json()) for i in items]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    return path
