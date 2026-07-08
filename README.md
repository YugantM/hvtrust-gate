# HVTrust Gate

Fail CI when an AI agent, framework, or MCP server your project depends on
falls below your trust threshold on [HVTracker](https://hvtracker.net) —
independent, evidence-based supply-chain trust scores for 300+ open-source
AI agents (OSSF Scorecard, build provenance, signed commits, runtime
capability surface; methodology public, credentials Ed25519-signed).

No API key. No install. One step.

## Usage

```yaml
- name: AI-dependency trust gate
  uses: YugantM/hvtrust-gate@v1
  with:
    targets: |
      langchain-ai/langgraph
      crewai
      n8n-io/n8n
    min-grade: C          # A | B | C | D
```

The job fails if any target's grade is below `min-grade`, with a summary
table in the run and errors annotated per finding:

| Target | Verdict | HVTrust | Note |
|---|---|---|---|
| [LangGraph](https://hvtracker.net/agents/langgraph/) | ✅ grade A | 88.1 | ok |
| some/agent | ❌ grade D | 41.0 | below minimum C |

## Inputs

| Input | Default | Meaning |
|---|---|---|
| `targets` | required | Comma- or newline-separated. GitHub `owner/repo`, repo URL, npm or PyPI package, HVTracker slug, or display name. |
| `min-grade` | `C` | Minimum acceptable trust grade (A ≥80 · B ≥65 · C ≥50 · else D). |
| `fail-on-untracked` | `false` | Fail when a target isn't in the registry (default: warn only — absence of evidence isn't evidence of harm). |
| `warn-only` | `false` | Report findings without failing the job. |

## Outputs

`failures` (count) and `report` (markdown table rows).

## How it works

Each target is resolved through HVTracker's public verify endpoint
(`/api/v1/mcp/verify` — the same resolution as [hvtracker.net/verify](https://hvtracker.net/verify)):
name, repo, or package → tracked agent → current grade and HVTrust score.
Network errors or an HVTracker outage never fail your build (targets show a
⚠️ warning instead).

Scores update continuously from public signals; the scoring methodology and
per-adjustment values are public at
[hvtracker.net/methodology](https://hvtracker.net/methodology). Every score
ships as an Ed25519-signed credential you can
[verify yourself](https://hvtracker.net/methodology/#verify-yourself).

Data: CC BY 4.0 · attribution "HVTracker (hvtracker.net)".
