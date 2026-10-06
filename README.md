# HVTrust Gate

See the trust grade of every AI agent, framework and MCP server your repository
depends on, scored by [HVTracker](https://hvtracker.net): independent,
evidence-based supply-chain trust scores for 1,700+ open-source AI projects
(OSSF Scorecard, build provenance, signed commits, advisories; methodology
public, credentials Ed25519-signed).

No API key. No install. No config.

## Quick start: zero config

```yaml
# .github/workflows/hvtrust.yml
name: HVTrust
on: [pull_request]
permissions:
  contents: read
  pull-requests: write   # lets it comment on PRs
jobs:
  trust:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: YugantM/hvtrust-gate@v1
```

With no `targets`, it finds your dependency files (`requirements*.txt`,
`pyproject.toml`, `package.json`, and MCP client configs: `.mcp.json`,
`.vscode/mcp.json`, `.cursor/mcp.json`), and reports the AI agents and MCP
servers HVTracker scores. Ordinary packages it doesn't track (`requests`,
`express`) are counted, never flagged.

When a pull request changes one of those files, it posts one comment, updated
on every run rather than duplicated:

> ### 🛡️ HVTrust Gate
> Minimum grade: **C** · 3 AI dependencies checked · 6 other packages not in the registry · 0 failures
>
> | Dependency | Verdict | HVTrust | Note |
> |---|---|---|---|
> | [langchain-ai/langgraph](https://hvtracker.net/agents/langgraph/) (`langgraph`) · `requirements.txt` | ✅ grade A | 93.2 | ok |
> | [PrefectHQ/fastmcp](https://hvtracker.net/agents/fastmcp/) (`fastmcp`) · `pyproject.toml` | ✅ grade B | 79.9 | ok |

Results also appear in the job summary. The job fails if a dependency's grade
is below `min-grade` (default C); set `warn-only: true` to only report.

## Gate specific dependencies

```yaml
- name: AI-dependency trust gate
  uses: YugantM/hvtrust-gate@v1
  with:
    targets: |
      langchain-ai/langgraph
      crewai
      n8n-io/n8n
    min-grade: B          # A | B | C | D
```

## Inputs

| Input | Default | Meaning |
|---|---|---|
| `targets` | empty | Comma- or newline-separated. GitHub `owner/repo`, repo URL, npm or PyPI package, HVTracker slug, or display name. Empty means scan the repo. |
| `scan` | `auto` | With no `targets`: `auto` finds dependency files; or list specific files; `false` disables. |
| `comment` | `auto` | PR comment: `auto` when the PR changes a scanned file, `always`, or `false`. Needs `pull-requests: write`; on forks the token is read-only, so it only warns. |
| `github-token` | `${{ github.token }}` | Token used for the PR comment. |
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
