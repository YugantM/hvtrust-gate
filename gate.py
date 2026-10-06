#!/usr/bin/env python3
"""HVTrust Gate — CI trust threshold for AI agent dependencies.

Two modes:
- `targets` given: check each one through HVTracker's public verify endpoint.
- no `targets` (default): find the repo's dependency files (requirements*.txt,
  pyproject.toml, package.json, MCP client configs), send the names to the
  public scan endpoint, and report the AI agents / MCP servers HVTracker
  scores. Ordinary packages it doesn't track are counted, not flagged.

On pull requests that change a scanned dependency file, it can post (and keep
updating) one comment with the results. Stdlib only, no API key.
"""
import glob
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://hvtracker.net/api/v1"
UA = {"User-Agent": "hvtrust-gate/1.1 (+https://github.com/YugantM/hvtrust-gate)"}
GRADE_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3}
SCAN_CHUNK = 60  # the scan endpoint's per-request item cap
MARKER = "<!-- hvtrust-gate -->"
SKIP_DIRS = {"node_modules", ".venv", "venv", ".git", "dist", "build", "site-packages"}
MCP_CONFIGS = [".mcp.json", "mcp.json", ".cursor/mcp.json", ".vscode/mcp.json",
               "claude_desktop_config.json"]


# ---- HTTP -----------------------------------------------------------------

def _request(url, data=None, headers=None, method=None):
    body = json.dumps(data).encode() if data is not None else None
    hdrs = {**UA, **(headers or {})}
    if body is not None:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


def verify(target):
    return _request(f"{API}/mcp/verify?server={urllib.parse.quote(target, safe='')}")


def scan(names):
    """Scan identifiers in chunks. Sent as a package.json-shaped object so
    scoped npm names (@scope/pkg) and URLs survive the server's parser."""
    out = {}
    for i in range(0, len(names), SCAN_CHUNK):
        chunk = names[i:i + SCAN_CHUNK]
        payload = json.dumps({"dependencies": {n: "*" for n in chunk}})
        res = _request(f"{API}/scan", data={"input": payload})
        for r in (res or {}).get("results", []):
            out[r["input"].lower()] = r
    return out


# ---- dependency files -----------------------------------------------------

def _name_from_spec(spec):
    """'langgraph[all]>=0.2; python_version>"3.9"' -> 'langgraph'."""
    spec = spec.strip()
    if not spec or spec.startswith(("#", "-")):
        return None
    m = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)", spec)
    return m.group(1) if m else None


def parse_requirements(text):
    return [n for n in (_name_from_spec(line.split("#", 1)[0]) for line in text.splitlines()) if n]


def parse_pyproject(text):
    import tomllib  # Python 3.11+; imported here so older runners only lose pyproject support
    data = tomllib.loads(text)
    names = []
    project = data.get("project", {})
    names += project.get("dependencies", [])
    for group in project.get("optional-dependencies", {}).values():
        names += group
    for group in data.get("dependency-groups", {}).values():
        names += [g for g in group if isinstance(g, str)]
    poetry = data.get("tool", {}).get("poetry", {})
    names += [k for k in poetry.get("dependencies", {}) if k.lower() != "python"]
    for grp in poetry.get("group", {}).values():
        names += list(grp.get("dependencies", {}))
    return [n for n in (_name_from_spec(s) for s in names) if n]


def parse_package_json(text):
    data = json.loads(text)
    names = []
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        names += list((data.get(key) or {}).keys())
    return names


def parse_mcp_config(text):
    """mcpServers (Claude, Cursor) or servers (VS Code): server names, remote
    URLs, and the package an npx/uvx/pipx command launches."""
    data = json.loads(text)
    servers = data.get("mcpServers") or data.get("servers") or {}
    names = []
    for key, cfg in servers.items():
        found = []
        if isinstance(cfg, dict):
            if isinstance(cfg.get("url"), str):
                found.append(cfg["url"])
            cmd = os.path.basename(str(cfg.get("command", "")))
            args = [a for a in cfg.get("args", []) if isinstance(a, str)]
            if cmd in ("npx", "uvx", "pipx", "bunx", "pnpx"):
                pkg = next((a for a in args if not a.startswith("-") and a != "run"), None)
                if pkg:
                    found.append(re.sub(r"(?<=.)@[^/]*$", "", pkg))  # drop @version
        # The server's key ("memory", "github") is a local label that can match an
        # unrelated agent by name; fall back to it only when nothing better exists.
        names += found or [key]
    return names


def find_dependency_files(root, patterns):
    if patterns:
        return [p for p in patterns if os.path.isfile(os.path.join(root, p))]
    found = []
    for pat in ["requirements*.txt", "*/requirements*.txt", "pyproject.toml", "*/pyproject.toml",
                "package.json", "*/package.json"] + MCP_CONFIGS:
        for p in sorted(glob.glob(os.path.join(root, pat))):
            rel = os.path.relpath(p, root)
            if not SKIP_DIRS.intersection(rel.split(os.sep)) and rel not in found:
                found.append(rel)
    return found


def collect(root, files):
    """{identifier_lower: (identifier, [files])} from the given files."""
    found = {}
    for rel in files:
        try:
            with open(os.path.join(root, rel), encoding="utf-8") as f:
                text = f.read()
            base = os.path.basename(rel)
            if base == "pyproject.toml":
                names = parse_pyproject(text)
            elif base == "package.json":
                names = parse_package_json(text)
            elif base.endswith(".json"):
                names = parse_mcp_config(text)
            else:
                names = parse_requirements(text)
        except Exception as e:
            print(f"::warning::hvtrust-gate: could not parse {rel}: {e}")
            continue
        for n in names:
            entry = found.setdefault(n.lower(), (n, []))
            if rel not in entry[1]:
                entry[1].append(rel)
    return found


# ---- PR comment -----------------------------------------------------------

def _pr_context():
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    if event not in ("pull_request", "pull_request_target"):
        return None
    try:
        with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as f:
            number = json.load(f)["pull_request"]["number"]
    except Exception:
        return None
    return os.environ.get("GITHUB_API_URL", "https://api.github.com"), os.environ["GITHUB_REPOSITORY"], number


def _gh(url, token, data=None, method=None):
    return _request(url, data=data, method=method,
                    headers={"Authorization": f"Bearer {token}",
                             "Accept": "application/vnd.github+json"})


def pr_changed_files(ctx, token):
    api, repo, number = ctx
    files, page = [], 1
    while page <= 10:
        batch = _gh(f"{api}/repos/{repo}/pulls/{number}/files?per_page=100&page={page}", token) or []
        files += [f["filename"] for f in batch]
        if len(batch) < 100:
            break
        page += 1
    return files


def upsert_comment(ctx, token, body):
    api, repo, number = ctx
    comments = _gh(f"{api}/repos/{repo}/issues/{number}/comments?per_page=100", token) or []
    mine = next((c for c in comments if MARKER in (c.get("body") or "")), None)
    if mine:
        _gh(f"{api}/repos/{repo}/issues/comments/{mine['id']}", token, {"body": body}, "PATCH")
    else:
        _gh(f"{api}/repos/{repo}/issues/{number}/comments", token, {"body": body}, "POST")


# ---- main -----------------------------------------------------------------

def _env_bool(name):
    return (os.environ.get(name) or "").strip().lower() == "true"


def _row(label, v, min_grade, files=None):
    grade, score = v.get("grade"), v.get("trust_score")
    link = f"https://hvtracker.net/agents/{v['slug']}/" if v.get("slug") else ""
    name = f"[{v.get('resolved') or label}]({link})" if link else (v.get("resolved") or label)
    where = f" · `{', '.join(files)}`" if files else ""
    if GRADE_ORDER.get(grade, 99) > GRADE_ORDER[min_grade]:
        return f"| {name} (`{label}`){where} | ❌ grade {grade} | {score} | below minimum {min_grade} |", True
    return f"| {name} (`{label}`){where} | ✅ grade {grade} | {score} | ok |", False


def main():
    root = os.environ.get("GITHUB_WORKSPACE") or os.getcwd()
    raw = os.environ.get("HVT_TARGETS", "")
    targets = [t.strip() for chunk in raw.splitlines() for t in chunk.split(",") if t.strip()]
    min_grade = (os.environ.get("HVT_MIN_GRADE") or "C").strip().upper()
    fail_on_untracked = _env_bool("HVT_FAIL_ON_UNTRACKED")
    warn_only = _env_bool("HVT_WARN_ONLY")
    scan_setting = (os.environ.get("HVT_SCAN") or "auto").strip()
    comment_mode = (os.environ.get("HVT_COMMENT") or "auto").strip().lower()
    token = os.environ.get("HVT_TOKEN", "")

    if min_grade not in GRADE_ORDER:
        print(f"::error::hvtrust-gate: invalid min-grade {min_grade!r} (use A/B/C/D)")
        return 1

    lines, failures, other, scanned_files = [], 0, 0, []
    if targets:
        for target in targets:
            try:
                v = verify(target)
            except Exception as e:  # network/5xx: never break CI on our outage
                lines.append(f"| {target} | ⚠️ check failed | — | {e} |")
                print(f"::warning::hvtrust-gate: could not check {target}: {e}")
                continue
            if not v.get("tracked"):
                status = "❌ untracked" if fail_on_untracked else "⚠️ untracked"
                failures += 1 if fail_on_untracked else 0
                lines.append(f"| {target} | {status} | — | not in the registry — no independent evidence |")
                level = "error" if fail_on_untracked else "warning"
                print(f"::{level}::hvtrust-gate: {target} is not tracked by HVTracker")
                continue
            row, bad = _row(target, v, min_grade)
            lines.append(row)
            if bad:
                failures += 1
                print(f"::error::hvtrust-gate: {v.get('resolved') or target} is grade {v.get('grade')} "
                      f"(HVTrust {v.get('trust_score')}) — below your minimum {min_grade}")
    elif scan_setting.lower() != "false":
        patterns = [] if scan_setting.lower() == "auto" else \
            [p.strip() for chunk in scan_setting.splitlines() for p in chunk.split(",") if p.strip()]
        scanned_files = find_dependency_files(root, patterns)
        deps = collect(root, scanned_files)
        if not deps:
            print("hvtrust-gate: no dependency files found to scan")
        else:
            try:
                results = scan([name for name, _ in deps.values()])
            except Exception as e:
                print(f"::warning::hvtrust-gate: scan failed, skipping: {e}")
                results = {}
            for key, (name, files) in sorted(deps.items()):
                v = results.get(key)
                if not v or not v.get("tracked"):
                    other += 1
                    continue
                row, bad = _row(name, v, min_grade, files)
                lines.append(row)
                if bad:
                    failures += 1
                    print(f"::error file={files[0]}::hvtrust-gate: {v.get('resolved') or name} is grade "
                          f"{v.get('grade')} (HVTrust {v.get('trust_score')}) — below your minimum {min_grade}")
    else:
        print("::error::hvtrust-gate: no targets given and scan is disabled")
        return 1

    checked = len(targets) if targets else len(lines)
    header = ("| Dependency | Verdict | HVTrust | Note |\n|---|---|---|---|\n")
    report = "\n".join(lines)
    footer = ("Scores by [HVTracker](https://hvtracker.net): independent, evidence-based "
              "(CC BY 4.0). Grades: A ≥80 · B ≥65 · C ≥50 · else D.")
    stats = f"Minimum grade: **{min_grade}** · {checked} AI dependenc{'y' if checked == 1 else 'ies'} checked"
    if other:
        stats += f" · {other} other package{'s' if other != 1 else ''} not in the registry"
    stats += f" · {failures} failure{'s' if failures != 1 else ''}"

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(f"## HVTrust Gate\n\n{stats}\n\n")
            if lines:
                f.write(header + report + "\n\n")
            f.write(footer + "\n")

    out_path = os.environ.get("GITHUB_OUTPUT")
    if out_path:
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(f"failures={failures}\n")
            f.write("report<<HVT_EOF\n" + report + "\nHVT_EOF\n")

    # PR comment: only with something to show, and (in auto mode) only when the
    # PR touches a scanned dependency file, so unrelated PRs stay quiet.
    ctx = _pr_context()
    if ctx and token and lines and comment_mode in ("auto", "always"):
        try:
            touched = comment_mode == "always" or bool(
                scanned_files and set(scanned_files) & set(pr_changed_files(ctx, token)))
            if touched:
                body = (f"{MARKER}\n### 🛡️ HVTrust Gate\n\n{stats}\n\n{header}{report}\n\n"
                        f"<sub>{footer} Checked by "
                        f"[hvtrust-gate](https://github.com/YugantM/hvtrust-gate).</sub>\n")
                if os.environ.get("HVT_DRY_RUN") == "1":
                    print(body)
                else:
                    upsert_comment(ctx, token, body)
        except urllib.error.HTTPError as e:  # forks get a read-only token
            print(f"::warning::hvtrust-gate: could not comment on the PR ({e.code}); "
                  "grant `pull-requests: write` to enable comments")
        except Exception as e:
            print(f"::warning::hvtrust-gate: could not comment on the PR: {e}")

    if failures and not warn_only:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
