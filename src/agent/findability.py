"""Findability Locator — "where do I find X?"

Federates across everything a team records: explicit resources, Figma files,
roadmaps, confluence/decision logs, and owned components. Solves the
"scattered in folders, nobody knows where it lives" problem.
"""
from dataclasses import dataclass
from .similarity import jaccard, tokenize
from ..providers.factory import Providers


@dataclass
class FindResult:
    label: str       # what it is, e.g. "Research repo"
    name: str
    team: str
    url: str
    score: float
    kind: str        # resource type / source


class FindabilityLocator:
    def __init__(self, providers: Providers):
        self.p = providers

    def find(self, query: str, threshold: float = 0.12) -> list[FindResult]:
        q_tokens = tokenize(query)
        results: list[FindResult] = []

        def score(text: str) -> float:
            # blend jaccard with raw token-hit recall so short queries still match
            j = jaccard(query, text)
            hits = len(q_tokens & tokenize(text))
            recall = hits / max(1, len(q_tokens))
            return max(j, 0.6 * recall)

        for team in self.p.manifests.get_all_teams():
            # Explicit resource registry
            for r in team.resources:
                s = score(f"{r.name} {r.type} {r.description}")
                if s >= threshold:
                    results.append(FindResult(r.type.replace("-", " ").title(), r.name, team.team, r.url, s, "resource"))

            # Figma files
            for fdoc in team.figma_files:
                s = score(f"{fdoc.name} figma design file")
                if s >= threshold:
                    results.append(FindResult("Figma file", fdoc.name, team.team, fdoc.url, s, "figma"))

            # Roadmap
            if team.roadmap_link:
                s = score(f"{team.team} roadmap plan timeline schedule")
                if s >= threshold:
                    results.append(FindResult("Roadmap", f"{team.team} roadmap", team.team, team.roadmap_link, s, "roadmap"))

            # Design system library
            if team.design_system_library:
                s = score("design system library components tokens")
                if s >= threshold:
                    results.append(FindResult("Design system library", "Design system", team.team, team.design_system_library, s, "design-system"))

        # Confluence pages / decision logs
        for page in self.p.confluence.get_pages():
            s = score(f"{page.title} {page.content_summary} {' '.join(page.tags)}")
            if s >= threshold:
                label = "Decision log" if page.decision_log else "Doc"
                results.append(FindResult(label, page.title, page.team, page.url, s, "confluence"))

        results.sort(key=lambda r: r.score, reverse=True)
        # Dedupe by URL — a shared library referenced by many teams shows once
        seen, deduped = set(), []
        for r in results:
            if r.url in seen:
                continue
            seen.add(r.url)
            deduped.append(r)
        return deduped[:8]
