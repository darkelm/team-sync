# How is this different from Copilot / Claude-in-Slack / ChatGPT connectors?

The question you'll get asked most. Short answer, then the detail.

## One sentence

Copilot, Glean, ChatGPT connectors, and "Claude/ChatGPT-in-Slack" are **general assistants that retrieve and summarize your content.** SyncBot is a **purpose-built coordination engine that models your teams, work, and experiences as structured objects and computes things a chatbot fundamentally can't.**

## How those tools work (and their ceiling)

They all share one architecture: connect to your tools → index the content as **documents** → retrieve relevant chunks → summarize with an LLM. Great for *"summarize this channel," "find the doc about X," "what did this thread say."*

But they have no **model** of your org. To them, Jira is a pile of text, not a graph of teams → components → dependencies → journeys. So they cannot reliably do:

| Capability | Why a general assistant can't | Why SyncBot can |
|---|---|---|
| "Are these two teams about to collide?" | No dependency graph; it isn't stated in any document | Computed from the structured model |
| "Is the onboarding journey coherent across 3 teams?" | No concept of a "journey" or what "inconsistent" means | Journey objects + deterministic drift logic |
| Proactive Monday digest / drift alerts | Reactive — answers only when asked | Watches and pushes |
| "Are we upholding our experience principles?" | Principles aren't a retrievable fact; it's a computation over live signals | Maps live signals → principles |
| Trustworthy ground truth | RAG is probabilistic — can confidently invent a relationship | Drift / health / conflicts are computed, not guessed |
| Works when APIs are locked down | Copilot needs full M365 Graph access | Runs off plain exports (connectors-off) |

**The deepest gap is hallucinated relationships.** Ask Copilot "who depends on the auth service?" and it synthesizes a plausible answer from whatever text it retrieved — which may be wrong. SyncBot answers from a dependency graph or says "I don't know." For coordination, *confidently wrong* is worse than useless.

## Where the general tools are genuinely better (be honest)

- General Q&A, drafting, summarizing arbitrary content — not SyncBot's job
- Breadth of connectors, enterprise scale, security/compliance, polish
- Copilot's deep native access to your actual emails, Teams chats, and calendars

If someone just wants "summarize my unread Slack," point them at Copilot. We don't compete there.

## The framing that wins the argument

**We're not competing with the assistant — we're the domain brain it's missing.** Because SyncBot is an **MCP server**, you can plug *it* into Copilot, Claude, ChatGPT, or Cursor. They become the friendly front-end; SyncBot supplies the structured coordination intelligence they can't compute.

> "Copilot is a great mouth. This is the brain that actually knows how your teams and experiences fit together — and you can connect the two."

**The honest kicker:** you *could* try to rebuild this on Copilot Studio or a custom GPT — but you'd have to build this exact engine underneath it (the team/component/journey graph, drift detection, journey-coherence logic). The LLM is interchangeable and commoditized. **The structured engine is the moat.** That's what we built; that's what no connector gives you for free.

## 30-second verbal version

"Copilot and friends are retrieval — they search your docs and summarize. They don't have a model of your org, so they can't tell you two teams are about to collide, whether a journey is consistent across the teams that own it, or whether you're living up to your design principles — those are computations over a structured graph, not facts in a document. And they only answer when asked; we watch and push. The neat part: ours is an MCP server, so it plugs *into* Copilot or Claude — we're the coordination brain, they're the interface."
