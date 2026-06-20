#!/usr/bin/env python3
"""
SyncBot design↔code divergence demo — the WHOLE loop, end to end, on synthetic data.
No Figma credentials, no Anthropic key, no running server required.

It fires a real Figma LIBRARY_PUBLISH at the real webhook handler for a component that
diverges in the synthetic org (Atlas's DataTable: row-hover out of sync), then drives the
proposal through the real router commands — so you can watch detection → joint-artifact →
both-owner ping → claim → resolve → board-clears, exactly as it will behave once a live
Figma webhook is wired.

Usage:
    python demo_divergence_loop.py
"""
import os
import sys
import tempfile

# Env MUST be set before importing the bot (token verification + the proposals store path
# are read at import / construction time). SYNCBOT_TEST disables Slack token verification.
os.environ.setdefault("SYNCBOT_TEST", "1")
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-demo")
os.environ.setdefault("SLACK_APP_TOKEN", "xapp-demo")
os.environ.setdefault("SLACK_SIGNING_SECRET", "demo-secret")
os.environ["FIGMA_WEBHOOK_PASSCODE"] = "demo-passcode"
# Isolate the proposals ledger to a throwaway file so the demo never touches real state.
os.environ["SYNCBOT_PROPOSALS_PATH"] = os.path.join(tempfile.mkdtemp(prefix="syncbot-demo-"), "proposals.jsonl")

sys.path.insert(0, ".")
from fastapi.testclient import TestClient  # noqa: E402
import webhook_server  # noqa: E402
from router import handle_query  # noqa: E402

DESIGNER = "U_DANA_DESIGN"
DEV = "U_CORY_DEV"


def _rule(title: str) -> None:
    print("\n" + "─" * 64)
    print(f"  {title}")
    print("─" * 64)


def main() -> None:
    print("\n" + "=" * 64)
    print("  SyncBot — design↔code divergence loop (synthetic org, no creds)")
    print("=" * 64)

    providers = webhook_server.get_providers()

    # Capture every Slack post so we can SHOW the pings the loop sends.
    pings: list[tuple[str, str]] = []

    def _capture(channel: str, message: str, *a, **k):
        pings.append((channel, message))
        return True

    providers.slack.post_message = _capture
    client = TestClient(webhook_server.app)

    # 1 ── A designer publishes a Figma library update for a component that diverges.
    _rule("1. Designer publishes the DataTable library update (it diverges from code)")
    resp = client.post("/webhooks/figma", json={
        "event_type": "LIBRARY_PUBLISH",
        "passcode": "demo-passcode",
        "webhook_id": "demo-wh",
        "timestamp": "2026-06-20T12:00:00Z",
        "file_key": "atlas-design-system",
        "file_name": "Atlas Design System",
        "created": [{"name": "DataTable"}],
        "description": "Updated DataTable row hover to blue-50",
    })
    body = resp.json()
    print(f"webhook → {resp.status_code}  dispatched={body.get('dispatched')}  "
          f"proposals_opened={body.get('proposals_opened')}")

    # 2 ── SyncBot opened a joint artifact and pinged BOTH owners (not one-way).
    _rule("2. SyncBot opens a joint artifact and pings BOTH owners")
    joint = [(c, m) for c, m in pings if "Design↔code proposal" in m]
    if not joint:
        print("(no proposal ping captured — is DataTable still a synthetic divergence?)")
    for channel, message in joint:
        print(f"\n  → {channel}:")
        for line in message.splitlines():
            print(f"      {line}")

    # 3 ── Anyone can see the open board.
    _rule("3. Anyone checks the board:  @syncbot proposals")
    print(handle_query("proposals"))

    # 4 ── A dev claims it.
    _rule("4. A dev claims it:  @syncbot claim DataTable")
    print(handle_query("claim DataTable", actor=DEV))

    # 5 ── ...and resolves it (code moved to match the design).
    _rule("5. ...and resolves it:  @syncbot resolve DataTable code-updated")
    print(handle_query("resolve DataTable code-updated", actor=DEV))

    # 6 ── The board clears; progress reflects the close-out (audited to the human).
    _rule("6. The board now (resolved drops off) + resolution progress")
    print(handle_query("proposals"))
    print()
    print(handle_query("resolution progress"))

    print("\n" + "=" * 64)
    print("  That's the full loop. Live Figma only changes WHERE the divergence in")
    print("  step 1 comes from — everything after is exactly what you just saw.")
    print("=" * 64 + "\n")


if __name__ == "__main__":
    main()
