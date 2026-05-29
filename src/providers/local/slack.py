from typing import Optional
from ..base import SlackProvider


class LocalSlackProvider(SlackProvider):
    """Prints to stdout instead of posting to Slack. Swap for LiveSlackProvider when ready."""

    def post_message(self, channel: str, text: str, blocks: Optional[list] = None) -> bool:
        print(f"\n[SLACK → {channel}]\n{text}\n")
        return True

    def post_digest(self, channel: str, digest_text: str) -> bool:
        print(f"\n[SLACK DIGEST → {channel}]\n{digest_text}\n")
        return True
