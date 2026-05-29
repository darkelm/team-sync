"""Background scheduler — fires the weekly digest automatically on a cron schedule.

Runs inside the bot process so a single deploy handles both reactive Q&A
and proactive digests.
"""
import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from ..providers.factory import Providers
from .digest import DigestGenerator


def _parse_cron(expr: str) -> CronTrigger:
    """Parse a standard 5-field cron string into an APScheduler trigger."""
    minute, hour, dom, month, dow = expr.split()
    return CronTrigger(minute=minute, hour=hour, day=dom, month=month, day_of_week=dow)


class DigestScheduler:
    def __init__(self, providers: Providers, config_path: str = "config.yaml"):
        self.providers = providers
        self.generator = DigestGenerator(providers)
        with open(config_path) as f:
            self.config = yaml.safe_load(f)
        self.scheduler = BackgroundScheduler(
            timezone=self.config.get("digest", {}).get("timezone", "UTC")
        )

    def _run_digests(self):
        print("[scheduler] Posting weekly digests to all team channels...", flush=True)
        self.generator.post_all_digests()
        print("[scheduler] Done.", flush=True)

    def start(self):
        cron_expr = self.config.get("digest", {}).get("schedule", "0 9 * * 1")
        trigger = _parse_cron(cron_expr)
        self.scheduler.add_job(self._run_digests, trigger, id="weekly_digest", replace_existing=True)
        self.scheduler.start()
        print(f"[scheduler] Weekly digest scheduled: '{cron_expr}' "
              f"({self.config.get('digest', {}).get('timezone', 'UTC')})", flush=True)

    def shutdown(self):
        self.scheduler.shutdown(wait=False)
