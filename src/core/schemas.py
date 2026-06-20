from __future__ import annotations
from datetime import date, datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────────────────

class TicketStatus(str, Enum):
    backlog = "backlog"
    todo = "todo"
    in_progress = "in_progress"
    in_review = "in_review"
    done = "done"
    blocked = "blocked"

class TicketPriority(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"

class PRStatus(str, Enum):
    open = "open"
    merged = "merged"
    closed = "closed"
    draft = "draft"

class DesignStatus(str, Enum):
    exploration = "exploration"
    in_progress = "in_progress"
    ready_for_review = "ready_for_review"
    approved = "approved"
    dev_ready = "dev_ready"

class DriftSeverity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"

class DevReadiness(str, Enum):
    """Design→dev handoff state for a Figma frame/section.

    This is the Figma-native coordination signal — whether a frame is marked
    "ready for dev" (Figma Dev Mode `ready_for_dev` status), still in design,
    awaiting review, or blocked. Distinct from DesignStatus (a component's own
    lifecycle) because it answers "can engineering pick this up?" specifically.
    """
    ready_for_dev = "ready_for_dev"
    in_design = "in_design"
    needs_review = "needs_review"
    blocked = "blocked"


# ── Team Manifest ─────────────────────────────────────────────────────────────

class TeamMember(BaseModel):
    name: str
    role: str
    slack_handle: str
    email: str

class CodeComponent(BaseModel):
    name: str
    path: str
    description: str

class DesignComponent(BaseModel):
    name: str
    figma_node_id: Optional[str] = None
    description: str

class TeamComponents(BaseModel):
    code: list[CodeComponent] = Field(default_factory=list)
    design: list[DesignComponent] = Field(default_factory=list)

class TeamDependency(BaseModel):
    team: str
    reason: str
    components: list[str] = Field(default_factory=list)

class FigmaFile(BaseModel):
    name: str
    url: str
    last_updated: Optional[date] = None

class Resource(BaseModel):
    """A findable thing a team owns: research repo, brand assets, prototype, doc, etc."""
    name: str
    type: str  # research | brand-assets | prototype | repo | doc | roadmap | figma | dashboard | other
    url: str
    description: str = ""

class TeamManifest(BaseModel):
    team: str
    description: str
    owner: TeamMember
    members: list[TeamMember] = Field(default_factory=list)
    slack_channel: str
    jira_project: str
    confluence_space: str
    figma_files: list[FigmaFile] = Field(default_factory=list)
    design_system_library: Optional[str] = None
    components: TeamComponents = Field(default_factory=TeamComponents)
    dependencies: list[TeamDependency] = Field(default_factory=list)
    roadmap_link: Optional[str] = None
    quarter_goals: list[str] = Field(default_factory=list)
    resources: list[Resource] = Field(default_factory=list)
    last_verified: Optional[date] = None


# ── Jira ─────────────────────────────────────────────────────────────────────

class Ticket(BaseModel):
    id: str
    title: str
    description: str
    status: TicketStatus
    priority: TicketPriority
    assignee: Optional[str] = None
    team: str
    labels: list[str] = Field(default_factory=list)
    due_date: Optional[date] = None
    created_at: datetime
    updated_at: datetime
    linked_tickets: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    epic: Optional[str] = None
    planned_quarter: Optional[str] = None


# ── Confluence ────────────────────────────────────────────────────────────────

class DecisionLog(BaseModel):
    id: str
    title: str
    decision: str
    rationale: str
    alternatives_considered: list[str] = Field(default_factory=list)
    decided_by: list[str]
    date: date
    status: str  # "approved", "superseded", "draft"
    related_tickets: list[str] = Field(default_factory=list)
    related_components: list[str] = Field(default_factory=list)
    team: str

class ConfluencePage(BaseModel):
    id: str
    title: str
    space: str
    team: str
    content_summary: str
    tags: list[str] = Field(default_factory=list)
    last_updated: date
    author: str
    url: str
    decision_log: Optional[DecisionLog] = None


# ── Strategy & Experience (above components/screens) ──────────────────────────

class Journey(BaseModel):
    """An end-to-end user experience that spans multiple teams (onboarding, checkout…)."""
    name: str
    description: str
    stages: list[str] = Field(default_factory=list)
    teams: list[str] = Field(default_factory=list)        # teams that touch this journey
    components: list[str] = Field(default_factory=list)   # components/areas that make it up
    owner: str = ""                                       # experience DRI
    north_star: str = ""                                  # the outcome this journey is judged by

class ExperiencePrinciple(BaseModel):
    """A design/experience principle that work should be measured against."""
    id: str
    name: str
    statement: str
    keywords: list[str] = Field(default_factory=list)     # used to map signals → this principle

class Outcome(BaseModel):
    """A measurable business or experience outcome the org is pursuing."""
    id: str
    name: str
    metric: str                                            # what is being measured
    target: str                                            # the goal value / threshold
    owner: str                                             # DRI (person or team)
    related_objectives: list[str] = Field(default_factory=list)  # OBJ-* ids
    related_journeys: list[str] = Field(default_factory=list)    # journey names

class ResearchInsight(BaseModel):
    """A finding from user research, usability study, or analytics."""
    id: str
    title: str
    summary: str
    source: str                                            # study name / report
    date: date
    themes: list[str] = Field(default_factory=list)       # keyword tags
    journeys: list[str] = Field(default_factory=list)     # journeys this informs
    teams: list[str] = Field(default_factory=list)        # teams it's relevant for
    url: str = ""                                          # link to full report


# ── Meetings / Transcripts ────────────────────────────────────────────────────

class ActionItem(BaseModel):
    owner: Optional[str] = None
    task: str
    due: Optional[str] = None
    quote: str = ""

class StrategySignal(BaseModel):
    """A high-level signal that should route beyond the meeting's own team.

    Unlike a formal decision, these are the moments in a meeting that change
    direction, reveal what success means, surface a creative breakthrough, or
    flag that two teams are unknowingly overlapping. They happen conversationally
    — the system pulls them out and routes them.
    """
    type: str   # metric_revealed | pivot | differentiation_risk | concept_breakthrough | duplicate_work
    description: str
    quote: str = ""
    broadcast: bool = True   # True = route to all teams on the initiative, not just adjacent ones


class MeetingNotes(BaseModel):
    id: str
    title: str
    date: date
    team: str
    participants: list[str] = Field(default_factory=list)
    teams_mentioned: list[str] = Field(default_factory=list)
    decisions: list[DecisionLog] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    strategy_signals: list[StrategySignal] = Field(default_factory=list)
    summary: str = ""


# ── GitHub ────────────────────────────────────────────────────────────────────

class PullRequest(BaseModel):
    id: str
    title: str
    description: str
    status: PRStatus
    author: str
    team: str
    base_branch: str
    head_branch: str
    files_changed: list[str] = Field(default_factory=list)
    components_touched: list[str] = Field(default_factory=list)
    created_at: datetime
    merged_at: Optional[datetime] = None
    linked_tickets: list[str] = Field(default_factory=list)
    cross_team_impact: list[str] = Field(default_factory=list)


# ── Figma ─────────────────────────────────────────────────────────────────────

class FigmaComponent(BaseModel):
    id: str
    name: str
    file_id: str
    file_name: str
    team: str
    description: str
    status: DesignStatus
    last_modified: datetime
    variants: list[str] = Field(default_factory=list)
    used_by_teams: list[str] = Field(default_factory=list)
    is_library_component: bool = False
    diverges_from_library: bool = False
    divergence_notes: Optional[str] = None


# ── Figma-native coordination signals (design↔dev handoff) ───────────────────
#
# These model the signals that actually drive design↔dev coordination, beyond
# drift: which frames are ready for dev, which open comments are blocking, what
# changed recently, and which tickets a frame is linked to. They are ADDITIVE
# and do not feed the drift detector.

class FigmaDevStatus(BaseModel):
    """Dev-handoff readiness for a Figma frame/section within a team's file.

    Mirrors Figma Dev Mode's per-node "Ready for dev" status plus the linked
    tickets a frame carries (Dev Mode "Links to" / branch annotations). One
    row per frame, keyed by node_id within the file.
    """
    node_id: str                                       # file-scoped Figma node id ("123:456")
    name: str                                          # frame/section name
    file_id: str
    file_name: str
    team: str
    readiness: DevReadiness
    last_modified: datetime
    linked_tickets: list[str] = Field(default_factory=list)  # Jira/issue keys linked to this frame
    assignee: Optional[str] = None                     # who marked it / owns the handoff
    notes: Optional[str] = None                        # status note / blocker reason


class FigmaComment(BaseModel):
    """An open comment thread on a Figma file (the Figma /comments API).

    SyncBot surfaces high-priority, unresolved comments because they are the
    realtime "this is blocking handoff" signal that drift never captures.
    """
    id: str                                            # Figma comment id
    file_id: str
    file_name: str
    team: str
    author: str
    message: str
    created_at: datetime
    resolved: bool = False
    priority: TicketPriority = TicketPriority.medium   # inferred from labels/keywords
    node_id: Optional[str] = None                      # frame the comment is anchored to
    mentions: list[str] = Field(default_factory=list)  # @-mentioned handles


class FigmaChange(BaseModel):
    """A recent version/frame change on a team's Figma file (the /versions API,
    plus frame-level last_modified deltas).

    Used to answer "what moved in design since the last digest?" — the temporal
    coordination signal.
    """
    id: str                                            # version id or change id
    file_id: str
    file_name: str
    team: str
    label: str                                         # version label / change summary
    description: str = ""
    changed_at: datetime
    author: str = ""
    affected_frames: list[str] = Field(default_factory=list)  # frame/section names touched


# ── Drift + Conflicts ─────────────────────────────────────────────────────────

class DriftIssue(BaseModel):
    id: str
    type: str  # "code_drift", "design_drift", "missing_decision_log", "dep_conflict"
    severity: DriftSeverity
    title: str
    description: str
    teams_involved: list[str]
    components_involved: list[str]
    detected_at: datetime
    suggested_action: str

class ConflictPrediction(BaseModel):
    id: str
    title: str
    description: str
    teams_involved: list[str]
    tickets_involved: list[str]
    components_at_risk: list[str]
    predicted_collision_date: Optional[date] = None
    severity: DriftSeverity
    suggested_action: str


# ── Digest ────────────────────────────────────────────────────────────────────

class TeamDigest(BaseModel):
    team: str
    week_of: date
    dev_updates: list[str] = Field(default_factory=list)
    design_updates: list[str] = Field(default_factory=list)
    dependency_changes: list[str] = Field(default_factory=list)
    open_conflicts: list[DriftIssue] = Field(default_factory=list)
    predicted_conflicts: list[ConflictPrediction] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    staleness: str | None = None  # manifest-freshness note, surfaced in the digest footer
