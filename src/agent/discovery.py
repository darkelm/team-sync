"""Collaborator Discovery + Reuse Radar.

Both surface connections teams haven't noticed:
- Discovery: who SHOULD be talking but isn't (related work, no recorded dependency)
- Reuse Radar: has someone already built/researched this?
"""
from dataclasses import dataclass
from .similarity import jaccard, overlap_terms
from ..providers.factory import Providers


@dataclass
class CollaborationSuggestion:
    team_a: str
    team_b: str
    reason: str
    evidence: list[str]
    already_linked: bool


@dataclass
class ReuseMatch:
    kind: str           # "component" | "ticket" | "design"
    name: str
    owning_team: str
    score: float
    overlap: list[str]
    detail: str


class CollaboratorDiscovery:
    def __init__(self, providers: Providers):
        self.p = providers

    def _linked(self, team_a, team_b) -> bool:
        a = self.p.manifests.get_team(team_a)
        b = self.p.manifests.get_team(team_b)
        a_deps = {d.team for d in a.dependencies} if a else set()
        b_deps = {d.team for d in b.dependencies} if b else set()
        return team_b in a_deps or team_a in b_deps

    def find_suggestions(self, threshold: float = 0.22) -> list[CollaborationSuggestion]:
        teams = self.p.manifests.get_all_teams()
        active: dict[str, list] = {}
        comps: dict[str, set] = {}
        for t in teams:
            active[t.team] = [tk for tk in self.p.jira.get_tickets(t.team) if tk.status.value != "done"]
            comps[t.team] = {c.name.lower() for c in t.components.code + t.components.design}

        suggestions: list[CollaborationSuggestion] = []
        names = [t.team for t in teams]
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = names[i], names[j]
                evidence = []

                # Shared components touched by active tickets (precise signal)
                a_tc = {c.lower() for tk in active[a] for c in tk.components}
                b_tc = {c.lower() for tk in active[b] for c in tk.components}
                shared_comp = (a_tc & b_tc) | (a_tc & comps[b]) | (b_tc & comps[a])
                if shared_comp:
                    evidence.append(f"Both have active work touching: {', '.join(sorted(shared_comp))}")

                # Best matching ticket pair (sensitive to a single shared theme)
                best = (0.0, None, None)
                for ta in active[a]:
                    for tb in active[b]:
                        s = jaccard(f"{ta.title} {ta.description} {' '.join(ta.labels)}",
                                    f"{tb.title} {tb.description} {' '.join(tb.labels)}")
                        if s > best[0]:
                            best = (s, ta, tb)
                if best[0] >= threshold and best[1] and best[2]:
                    terms = overlap_terms(f"{best[1].title} {best[1].description}",
                                          f"{best[2].title} {best[2].description}")[:5]
                    evidence.append(
                        f"Similar tickets: `{best[1].id}` ↔ `{best[2].id}` "
                        f"(both about: {', '.join(terms)})"
                    )

                if evidence:
                    linked = self._linked(a, b)
                    reason = ("Related work and already connected — keep each other posted."
                              if linked else
                              "Related work but NOT listed as dependencies — you may be missing a needed collaboration.")
                    suggestions.append(CollaborationSuggestion(
                        team_a=a, team_b=b, reason=reason, evidence=evidence, already_linked=linked,
                    ))

        suggestions.sort(key=lambda s: s.already_linked)  # discoveries (unlinked) first
        return suggestions


class ReuseRadar:
    def __init__(self, providers: Providers):
        self.p = providers

    def search(self, description: str, exclude_team: str = "", threshold: float = 0.2) -> list[ReuseMatch]:
        matches: list[ReuseMatch] = []

        # Components (code + design) across teams
        for team in self.p.manifests.get_all_teams():
            if exclude_team and team.team == exclude_team:
                continue
            for c in team.components.code:
                score = jaccard(description, f"{c.name} {c.description}")
                if score >= threshold:
                    matches.append(ReuseMatch("component", c.name, team.team, score,
                                              overlap_terms(description, f"{c.name} {c.description}"),
                                              c.description))
            for c in team.components.design:
                score = jaccard(description, f"{c.name} {c.description}")
                if score >= threshold:
                    matches.append(ReuseMatch("design", c.name, team.team, score,
                                              overlap_terms(description, f"{c.name} {c.description}"),
                                              c.description))

        # Existing tickets (someone may already be doing this)
        for tk in self.p.jira.get_tickets():
            if exclude_team and tk.team == exclude_team:
                continue
            score = jaccard(description, f"{tk.title} {tk.description}")
            if score >= threshold:
                matches.append(ReuseMatch("ticket", f"{tk.id}: {tk.title}", tk.team, score,
                                          overlap_terms(description, f"{tk.title} {tk.description}"),
                                          tk.status.value))

        matches.sort(key=lambda m: m.score, reverse=True)
        return matches[:8]
