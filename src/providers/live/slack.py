import os
from typing import Optional
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from ..base import SlackProvider


class LiveSlackProvider(SlackProvider):
    def __init__(self):
        self.client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])

    def post_message(self, channel: str, text: str, blocks: Optional[list] = None) -> bool:
        try:
            self.client.chat_postMessage(channel=channel, text=text, blocks=blocks)
            return True
        except SlackApiError:
            return False

    def post_digest(self, channel: str, digest_text: str) -> bool:
        return self.post_message(channel, digest_text)
