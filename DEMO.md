# Live Demo Cheat Sheet (90 seconds)

Bot runs in **#all-prototypetoolspilot** (workspace: PrototypeToolsPilot). Everything below is real output, no mocks. Runs with **zero AI** today; a key turns on natural language.

## Open with
> "It keeps every team in sync — catches when teams are about to collide, when work's being duplicated, and gives leadership a health read — all from data we already have, in plain English."

## Slack flow (type in the channel)

| Type this | It shows |
|---|---|
| `@syncbot portfolio status` | Leadership health of all teams — "what an MD sees Monday" |
| `@syncbot how's Team Horizon doing?` | Drill-in: status, plain risks, who to talk to |
| `@syncbot who should I talk to?` | **The aha** — Horizon & Forge both on the API gateway, not connected |
| `@syncbot has anyone already built a notification bell?` | Reuse — finds Nova's canonical one |
| `@syncbot who owns authh` | Typo still resolves → auth / Team Phoenix |
| `@syncbot scan for conflicts` | Drift, breaking changes w/o decisions, cross-team PR impact |
| `@syncbot prep me for a sync with Team Atlas and Team Forge` | Auto meeting briefing + agenda |

## The showstopper — capture a meeting live

In a terminal (screen-share it):
```
.venv/bin/python3 -m src.cli.main import data/exports/samples/cross-team-sync-2026-05-29.txt --team "Team Atlas"
```
→ extracts **2 decisions, 4 action items (with owners + due dates), 3 risks** from a raw transcript.

Then back in Slack — it's now searchable:
```
@syncbot what was decided about the schema?
@syncbot action items for Team Atlas
```
> "A decision made out loud in a meeting is now findable by anyone, forever."

## Experience-strategy altitude (for design/strategy audiences)

This is the higher-level story — coordination above components/screens:
```
@syncbot how's the onboarding journey?
@syncbot show me all the experience journeys
@syncbot are we upholding our experience principles?
```
→ journey-level *coherence across the teams that shape it* (not per-component), and whether live signals (inconsistencies, undocumented decisions, collisions) are working against the org's stated **experience principles**.
> "This is the part that matters to experience strategy — is the *journey* coherent across everyone who touches it, and are we living up to our own principles?"

## Audience framing (optional flourish)
```
@syncbot I'm an MD
@syncbot how's Team Atlas doing?
```
> "Same data, framed for whoever's asking — leadership gets health and risk, no jargon."

## Close with
> "All of this runs today with **no AI** — pure logic. Add a key and the same bot understands any phrasing. It's also an **MCP server**, so Cursor / Claude Desktop / any AI tool can use the whole engine. And it ingests real Jira/Confluence/GitHub **exports** — works even when enterprise IT has the APIs locked down."

## If asked
- **Where's it running?** My laptop now; Railway is the 24/7 step (config ready).
- **Is this mocked?** No — real engine on a synthetic 5-team org with realistic conflicts baked in.
- **AI?** Keyword mode now; natural language is one API key away. Strong without AI by design.

## Terminal-only backup (one shot)
```
.venv/bin/python3 demo.py
```
Prints the full drift scan, predicted conflicts, and a sample weekly digest.
