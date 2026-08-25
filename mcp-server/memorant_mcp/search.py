"""Full-text search over 书童 · Memorant using ripgrep.

Falls back to Python glob+grep if `rg` is not on PATH, so the server works
without rg installed (just slower). Results are post-filtered by project
frontmatter when `project` is given — project filtering always goes through
frontmatter, never filename parsing (per the four-category project model).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

from .naming import vault_root


def _rg_available() -> bool:
    return shutil.which("rg") is not None


def _search_rg(query: str, dirs: list[str], limit: int) -> list[dict]:
    root = vault_root()
    paths = [os.path.join(root, d) for d in dirs] or [root]
    cmd = [
        "rg",
        "--json",
        "-i",
        "--max-count", "3",
        "-g", "*.md",
        "--",
        query,
        *paths,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    results: list[dict] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "match":
            continue
        data = obj.get("data", {})
        # Use the full matched line as the snippet, not the concatenation of
        # all submatches (which would repeat the query word N times on a
        # match-heavy line, e.g. "VAULT_ROOTVAULT_ROOT...").
        text = data.get("lines", {}).get("text", "")
        if not text:
            # Fallback to first submatch if line text is absent.
            hits = data.get("submatches", [])
            text = hits[0].get("match", {}).get("text", "") if hits else ""
        abs_path = data.get("path", {}).get("text", "")
        rel = os.path.relpath(abs_path, root) if abs_path else ""
        line_no = data.get("line_number")
        results.append({"file": rel, "line": line_no, "match": text.strip()})
        if len(results) >= limit:
            break
    return results


def _search_fallback(query: str, dirs: list[str], limit: int) -> list[dict]:
    """Pure-python fallback when rg is missing."""
    root = vault_root()
    needle = query.lower()
    results: list[dict] = []
    search_dirs = [os.path.join(root, d) for d in dirs] or [root]
    for base in search_dirs:
        for dirpath, _dirs, files in os.walk(base):
            for fn in files:
                if not fn.endswith(".md"):
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    with open(p, encoding="utf-8") as f:
                        for i, line in enumerate(f, 1):
                            if needle in line.lower():
                                rel = os.path.relpath(p, root)
                                results.append({"file": rel, "line": i, "match": line.strip()[:200]})
                                break
                except OSError:
                    continue
                if len(results) >= limit:
                    return results
    return results


def _matches_project(rel_path: str, project: str) -> bool:
    """Check a file's frontmatter `project` field against the filter.

    Daily stores project as a list; others as a string. Accept either form.
    """
    root = vault_root()
    abs_path = os.path.join(root, rel_path)
    try:
        with open(abs_path, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return False
    # Crude frontmatter parse — only need the project field.
    if not content.startswith("---"):
        return False
    end = content.find("\n---", 3)
    if end == -1:
        return False
    fm = content[3:end]
    for line in fm.splitlines():
        line = line.strip()
        if line.lower().startswith("project:"):
            val = line.split(":", 1)[1].strip().strip("[]")
            projects = [p.strip().strip('"\'') for p in val.split(",") if p.strip()]
            return project in projects
    return False


def search(
    query: str,
    dirs: list[str] | None = None,
    project: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Search Memorant Markdown for `query`, optionally filtering by directory/project."""
    dirs = dirs or ["bugs", "snippets", "daily", "arch"]
    if _rg_available():
        raw = _search_rg(query, dirs, limit * 3 if project else limit)
    else:
        raw = _search_fallback(query, dirs, limit * 3 if project else limit)

    if project:
        seen: set[str] = set()
        filtered: list[dict] = []
        for r in raw:
            if r["file"] in seen:
                continue
            if _matches_project(r["file"], project):
                filtered.append(r)
                seen.add(r["file"])
            if len(filtered) >= limit:
                break
        return filtered
    return raw[:limit]
