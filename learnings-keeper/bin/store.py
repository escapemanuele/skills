#!/usr/bin/env python3
"""
learnings-keeper config + path resolution.

The skill saves plain-markdown notes to one of two places:
- The user's Obsidian vault (autodetected via a `.obsidian/` folder).
- A default hidden folder at `~/.claude/skills/skills-daimon/learnings/`.

The choice is made once and recorded in:
    ~/.claude/skills/learnings-keeper/store.json

Commands:
    python3 store.py status              # prints current config + diagnostics
    python3 store.py autodetect-vault    # tries to find a vault; prints JSON
    python3 store.py set --kind obsidian --path /path/to/Vault/sub
    python3 store.py set --kind default
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

CONFIG_PATH = Path.home() / ".claude" / "skills" / "learnings-keeper" / "store.json"
DEFAULT_STORE = Path.home() / ".claude" / "skills" / "skills-daimon" / "learnings"
MANIFEST_NAME = ".skills-daimon.json"


def load() -> dict | None:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (OSError, ValueError):
        return None


def save(cfg: dict) -> dict:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")
    return cfg


def resolved_path(cfg: dict | None) -> Path:
    """Return the absolute path where notes should be written."""
    if cfg and cfg.get("path"):
        return Path(cfg["path"]).expanduser()
    return DEFAULT_STORE


def autodetect_vault() -> Path | None:
    """Find an Obsidian vault by walking likely user directories.

    Heuristic: look under ~/Documents, ~, ~/Vault, ~/Obsidian for a directory
    that contains a `.obsidian/` subdir. Returns the FIRST hit (caller decides).
    """
    home = Path.home()
    candidates: list[Path] = []
    for root in (home, home / "Documents", home / "Vault", home / "Obsidian"):
        if not root.is_dir():
            continue
        # Top-level: is the root itself a vault?
        if (root / ".obsidian").is_dir():
            candidates.append(root)
        # One level down
        try:
            for sub in sorted(root.iterdir()):
                if not sub.is_dir():
                    continue
                if (sub / ".obsidian").is_dir():
                    candidates.append(sub)
        except OSError:
            continue
        # Two levels (some users nest a vault inside ~/Documents/Notes/MyVault)
        try:
            for sub in sorted(root.iterdir()):
                if not sub.is_dir():
                    continue
                for sub2 in sorted(sub.iterdir()):
                    if not sub2.is_dir():
                        continue
                    if (sub2 / ".obsidian").is_dir():
                        candidates.append(sub2)
        except OSError:
            continue
    # Deduplicate while keeping order
    seen: set[str] = set()
    uniq: list[Path] = []
    for c in candidates:
        s = str(c.resolve())
        if s in seen:
            continue
        seen.add(s)
        uniq.append(c)
    return uniq[0] if uniq else None


def suggest_subfolder(vault: Path) -> str:
    """Pick a sensible learnings subfolder inside a vault.

    If the vault uses PARA-style numeric prefixes (e.g. `3. Resources`),
    return that path so the note feels native. Otherwise default to a plain
    `Claude/Learnings/`.
    """
    try:
        for entry in sorted(vault.iterdir()):
            if not entry.is_dir():
                continue
            name = entry.name
            lower = name.lower()
            if "resources" in lower and (name[:2].rstrip().isdigit() or name[:1].isdigit()):
                return f"{name}/Tech/Claude/Learnings"
        # Plain Resources (no numeric prefix)
        if (vault / "Resources").is_dir():
            return "Resources/Tech/Claude/Learnings"
    except OSError:
        pass
    return "Claude/Learnings"


def _cmd_status(args) -> int:
    cfg = load()
    path = resolved_path(cfg)
    print(json.dumps({
        "config_path": str(CONFIG_PATH),
        "config_exists": CONFIG_PATH.exists(),
        "config": cfg,
        "resolved_path": str(path),
        "resolved_exists": path.exists(),
    }, indent=2))
    return 0


def _cmd_autodetect(args) -> int:
    vault = autodetect_vault()
    out = {"vault": str(vault) if vault else None}
    if vault:
        out["suggested_subfolder"] = suggest_subfolder(vault)
        out["full_path"] = str(vault / out["suggested_subfolder"])
    print(json.dumps(out, indent=2))
    return 0


def _cmd_set(args) -> int:
    if args.kind not in ("obsidian", "default"):
        print(json.dumps({"error": "kind must be obsidian or default"}))
        return 1
    cfg: dict = {"kind": args.kind, "filename_pattern": "{date}-{slug}.md"}
    if args.kind == "obsidian":
        if not args.path:
            print(json.dumps({"error": "--path required for kind=obsidian"}))
            return 1
        p = Path(args.path).expanduser().resolve()
        cfg["path"] = str(p)
        # Note: we don't refuse to create the folder; the caller (save.py)
        # mkdirs on first write so a typo is correctable.
    else:
        cfg["path"] = str(DEFAULT_STORE)
    save(cfg)
    print(json.dumps({"saved": cfg}, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="learnings-keeper store config")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s_st = sub.add_parser("status")
    s_st.set_defaults(func=_cmd_status)

    s_ad = sub.add_parser("autodetect-vault")
    s_ad.set_defaults(func=_cmd_autodetect)

    s_set = sub.add_parser("set")
    s_set.add_argument("--kind", required=True, choices=("obsidian", "default"))
    s_set.add_argument("--path", default=None)
    s_set.set_defaults(func=_cmd_set)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
