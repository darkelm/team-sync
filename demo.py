#!/usr/bin/env python3
"""
SyncBot demo — runs a set of realistic queries against the synthetic org.
No credentials required for local mode.

Usage:
    python demo.py
    python demo.py --interactive   # chat mode
"""
import argparse
import os
import sys

sys.path.insert(0, ".")
from src.agent.syncbot import SyncBot
from src.agent.digest import DigestGenerator
from src.providers.factory import Providers
from src.agent.detector import DriftDetector

DEMO_QUESTIONS = [
    "Who owns the auth component?",
    "When is Team Atlas shipping their next deliverable?",
    "What was decided about the OAuth migration?",
    "Get me up to speed on Team Horizon as a new designer.",
    "Are there any design drift issues I should know about?",
    "Scan for all current conflicts and tell me what's most urgent.",
    "Which teams depend on Team Phoenix?",
    "What's the status of the NotificationBell component across teams?",
    "Is there a decision log for the API gateway migration?",
    "Prep me for a cross-team sync with Team Atlas and Team Forge.",
]


def run_demo(interactive: bool = False):
    print("\n" + "="*60)
    print("  SyncBot Demo — Synthetic Org (5 teams, real conflicts)")
    print("="*60 + "\n")

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("⚠  ANTHROPIC_API_KEY not set — running in local scan mode only.\n")
        run_local_scan()
        return

    bot = SyncBot("config.yaml")

    if interactive:
        print("Chat mode — type 'quit' to exit, 'reset' to clear history.\n")
        while True:
            try:
                question = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if question.lower() == "quit":
                break
            if question.lower() == "reset":
                bot.reset()
                print("History cleared.\n")
                continue
            if not question:
                continue
            answer = bot.ask(question, verbose=True)
            print(f"\nSyncBot: {answer}\n")
        return

    for i, question in enumerate(DEMO_QUESTIONS, 1):
        print(f"[{i}/{len(DEMO_QUESTIONS)}] {question}")
        print("-" * 50)
        answer = bot.ask(question, verbose=True)
        print(f"\n{answer}\n")
        bot.reset()
        print()


def run_local_scan():
    """Runs drift detection and digest without Claude API key."""
    providers = Providers("config.yaml")
    detector = DriftDetector(providers)

    print("── Drift & Conflict Scan ──────────────────────────────\n")
    issues = detector.run_all()
    for issue in issues:
        print(f"[{issue.severity.value.upper()}] {issue.title}")
        print(f"  Teams: {', '.join(issue.teams_involved)}")
        print(f"  → {issue.suggested_action}\n")

    print("\n── Predicted Conflicts ───────────────────────────────\n")
    conflicts = detector.predict_conflicts()
    for c in conflicts:
        print(f"[{c.severity.value.upper()}] {c.title}")
        print(f"  Teams: {', '.join(c.teams_involved)}")
        print(f"  → {c.suggested_action}\n")

    print("\n── Weekly Digest (Team Horizon) ──────────────────────\n")
    generator = DigestGenerator(providers)
    digest = generator.generate_for_team("Team Horizon")
    print(generator.format_slack_message(digest))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--interactive", "-i", action="store_true", help="Chat mode")
    args = parser.parse_args()
    run_demo(interactive=args.interactive)
