#!/usr/bin/env python3
"""Register and manage Figma webhooks for SyncBot.

Figma webhooks POST to your public webhook_server.py endpoint when a library
is published or a file changes. This script handles registration so you don't
have to curl it manually.

Usage:
    python figma_webhook_setup.py list
    python figma_webhook_setup.py register --url https://your-server.com --file-key abc123
    python figma_webhook_setup.py register --url https://your-server.com --team-id TEAM123
    python figma_webhook_setup.py delete --webhook-id 123456
    python figma_webhook_setup.py test --webhook-id 123456

Figma webhook event types we care about:
    LIBRARY_PUBLISH  — a shared library component was published (the big one)
    FILE_UPDATE      — any file was saved (noisier; use for specific files only)

Auth note:
    Personal access tokens (what FIGMA_ACCESS_TOKEN usually is) work for
    FILE_UPDATE on specific files. For LIBRARY_PUBLISH on team-wide libraries,
    you need an OAuth token with 'webhooks:write' scope. The script handles both.
"""
import os
import sys
import json
import argparse
import httpx
from dotenv import load_dotenv

load_dotenv()

BASE = "https://api.figma.com/v1"


def headers():
    token = os.environ.get("FIGMA_ACCESS_TOKEN", "")
    if not token:
        print("ERROR: FIGMA_ACCESS_TOKEN not set in .env")
        sys.exit(1)
    return {"X-Figma-Token": token, "Content-Type": "application/json"}


def list_webhooks():
    """List all webhooks registered to your Figma account."""
    r = httpx.get(f"{BASE}/webhooks/v2", headers=headers())
    if r.status_code == 200:
        hooks = r.json().get("webhooks", [])
        if not hooks:
            print("No webhooks registered.")
            return
        for h in hooks:
            print(f"  ID: {h['id']}")
            print(f"  Event: {h['event_type']}")
            print(f"  Endpoint: {h['endpoint']}")
            print(f"  Status: {h.get('status', 'unknown')}")
            if h.get("file_key"):
                print(f"  File: https://figma.com/file/{h['file_key']}")
            print()
    else:
        print(f"Error {r.status_code}: {r.text}")


def register_webhook(endpoint_url: str, passcode: str, file_key: str = "", team_id: str = "",
                     event_type: str = "LIBRARY_PUBLISH", description: str = "SyncBot coordination"):
    """Register a Figma webhook."""
    if not endpoint_url.startswith("https://"):
        print("ERROR: Figma requires HTTPS. For local dev, use ngrok: ngrok http 8001")
        print("       Then use the ngrok https URL.")
        sys.exit(1)

    payload = {
        "event_type": event_type,
        "endpoint": f"{endpoint_url.rstrip('/')}/webhooks/figma",
        "passcode": passcode,
        "description": description,
    }
    if file_key:
        payload["file_key"] = file_key
        print(f"Registering {event_type} webhook for file: {file_key}")
    elif team_id:
        payload["team_id"] = team_id
        print(f"Registering {event_type} webhook for team: {team_id}")
    else:
        print("ERROR: provide --file-key or --team-id")
        sys.exit(1)

    r = httpx.post(f"{BASE}/webhooks/v2", headers=headers(), json=payload)
    if r.status_code in (200, 201):
        hook = r.json()
        print(f"✅ Webhook registered — ID: {hook.get('id')}")
        print(f"   Posts to: {payload['endpoint']}")
        print(f"   Event: {event_type}")
        print(f"   Add to .env: FIGMA_WEBHOOK_PASSCODE={passcode}")
    else:
        print(f"Error {r.status_code}: {r.text}")


def delete_webhook(webhook_id: str):
    r = httpx.delete(f"{BASE}/webhooks/v2/{webhook_id}", headers=headers())
    print("✅ Deleted." if r.status_code == 200 else f"Error {r.status_code}: {r.text}")


def test_webhook(webhook_id: str):
    """Trigger a test ping from Figma to your endpoint."""
    r = httpx.post(f"{BASE}/webhooks/v2/{webhook_id}/ping", headers=headers())
    print("✅ Ping sent." if r.status_code in (200, 204) else f"Error {r.status_code}: {r.text}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manage Figma webhooks for SyncBot")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("list")

    reg = sub.add_parser("register")
    reg.add_argument("--url", required=True, help="Your public webhook server URL (must be HTTPS)")
    reg.add_argument("--file-key", default="", help="Figma file key (from the file URL)")
    reg.add_argument("--team-id", default="", help="Figma team ID (for team-wide library events)")
    reg.add_argument("--event", default="LIBRARY_PUBLISH", help="LIBRARY_PUBLISH or FILE_UPDATE")
    reg.add_argument("--passcode", default=os.environ.get("FIGMA_WEBHOOK_PASSCODE", "syncbot-secret"),
                     help="Passcode to verify incoming webhooks")

    d = sub.add_parser("delete")
    d.add_argument("--webhook-id", required=True)

    t = sub.add_parser("test")
    t.add_argument("--webhook-id", required=True)

    args = parser.parse_args()

    if args.cmd == "list":
        list_webhooks()
    elif args.cmd == "register":
        register_webhook(args.url, args.passcode, args.file_key, args.team_id, args.event)
    elif args.cmd == "delete":
        delete_webhook(args.webhook_id)
    elif args.cmd == "test":
        test_webhook(args.webhook_id)
    else:
        parser.print_help()
