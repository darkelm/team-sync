"""Background scheduler — fires the weekly digest automatically on a cron schedule.

Runs inside the bot process so a single deploy handles both reactive Q&A and
proactive digests. Registry-aware: every registered project gets its own digest
run against its own providers, so multi-client engagements stay isolated. The
schedule/timezone come from the default config; all projects run in that job.
"""
import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .digest import DigestGenerator


def _parse_cron(expr: str) -> CronTrigger:
    """Parse a standard 5-field cron string into an APScheduler trigger."""
    minute, hour, dom, month, dow = expr.split()
    return CronTrigger(minute=minute, hour=hour, day=dom, month=month, day_of_week=dow)


def _load_config(path: str) -> dict:
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except OSError:
        return {}


class DigestScheduler:
    def __init__(self, registry, default_config: str = "config.yaml"):
        """registry: a ProjectRegistry. Schedule/timezone come from default_config."""
        self.registry = registry
        self.default_config = default_config
        self.config = _load_config(default_config)
        self.scheduler = BackgroundScheduler(
            timezone=self.config.get("digest", {}).get("timezone", "UTC")
        )

    def _projects(self) -> list:
        """Every distinct project to run digests for: all registered + the default.

        Deduped by config path so a project that uses config.yaml isn't run twice.
        """
        projects = list(self.registry.all_projects())
        configs = {p.config for p in projects}
        default = self.registry.for_channel("")  # the fallback default Project
        if default.config not in configs:
            projects.append(default)
        return projects

    def _run_digests(self):
        projects = self._projects()
        print(f"[scheduler] Running weekly digests for {len(projects)} project(s)...", flush=True)
        for project in projects:
            try:
                providers = project.providers()
                res = DigestGenerator(providers).post_all_digests()
                print(f"[scheduler] {project.name}: {len(res['sent'])} sent, "
                      f"{len(res['failed'])} failed, {len(res['paused'])} paused.", flush=True)
                self._run_exec_digest(project, providers)
            except Exception as e:
                print(f"[scheduler] {project.name}: digest run failed: {e}", flush=True)
        print("[scheduler] Done.", flush=True)

    def _run_exec_digest(self, project, providers):
        """Post the leadership portfolio rollup to the project's exec channel, if set."""
        cfg = _load_config(project.config)
        exec_channel = cfg.get("leadership", {}).get("exec_channel", "")
        if not exec_channel:
            return
        from .health import HealthAssessor
        text = HealthAssessor(providers, project.config).format_portfolio()
        providers.slack.post_digest(exec_channel, text)
        print(f"[scheduler] {project.name}: exec rollup -> {exec_channel}", flush=True)

    def start(self):
        cron_expr = self.config.get("digest", {}).get("schedule", "0 9 * * 1")
        trigger = _parse_cron(cron_expr)
        self.scheduler.add_job(self._run_digests, trigger, id="weekly_digest", replace_existing=True)
        self.scheduler.start()
        tz = self.config.get("digest", {}).get("timezone", "UTC")
        print(f"[scheduler] Weekly digest scheduled: '{cron_expr}' ({tz}) "
              f"across {len(self._projects())} project(s).", flush=True)

    def shutdown(self):
        self.scheduler.shutdown(wait=False)
