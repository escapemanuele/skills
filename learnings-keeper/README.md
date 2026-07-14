# learnings-keeper

Saves what you figured out in a session — the bug you found, the decision you made, the library you picked — as short plain-markdown notes in a store you already live in (your Obsidian vault, or a default folder). Later, it **looks those notes up** when a similar problem comes around. That's the compounding.

Part of the [skills-daimon plugin](../skills-daimon/): the daimon report *diagnoses* ("you don't save what you learn"); this skill *treats it*. It also works standalone — "save what we learned", "remember this", "any past notes about X?".

## The one rule

**Never write to disk without showing the draft first.** Every save goes preview → confirm → write. No silent saves. A redactor masks plausible secrets before the preview; you are the last line of defense.

## How it works

### Configure the store (first run only)

One question: Obsidian vault or default folder? It autodetects a vault (a folder with `.obsidian/` under `~`, `~/Documents`, `~/Vault`, `~/Obsidian`) so you just confirm, and suggests a subfolder — PARA-aware (`<n>. Resources/Tech/Claude/Learnings/`) when it recognizes the layout, `Claude/Learnings/` otherwise. It only ever writes inside that explicit subfolder, never to the vault root.

### Extract → distill → save

1. **Extract** (`bin/extract.py`) pulls candidate sentences from the session transcript — matches learn-phrases in English/Italian/Spanish/French (*"we decided"*, *"the fix was"*, *"turns out"*, *"capito"*, *"la soluzione era"*…), already redacted. Sessions that didn't achieve anything are refused by default: failed sessions are not where compounding lives. Zero candidates → it says so honestly instead of fabricating a learning.
2. **Distill** — Claude reads the candidates and proposes 1–3 short notes, each with a title (≤80 chars), the thing figured out (1–2 sentences), optional stakes, and an optional "try next time". Real specifics are quoted — file paths, error messages, library names — because a vague learning is a useless one.
3. **Preview → confirm → save** — drafts shown inline; you answer *yes* / *pick 1,3* / *no*, or edit a draft in place. Accepted notes are written by `bin/save.py` as `<store>/YYYY-MM-DD-<slug>.md`, plus an entry in a manifest (`.skills-daimon.json`) you can audit or delete from. Obsidian stores get `[[wiki-link]]` see-also lines.

### Lookup (the payoff)

```bash
python3 bin/lookup.py --query "yarn cache" --limit 5    # optionally --repo <name>
```

Two triggers: you ask ("have we seen this CI thing before?"), or skills-daimon's coaching auto-surfaces a note when it detects recurring friction in a repo (*"`wrong_approach` ×6 in wp-calypso this month — you wrote a note about this on 2026-04-12"*). Uses ripgrep when available, falls back to plain glob+grep when not.

## Privacy

- The redactor (`bin/redact.py`) runs at **every disk boundary** — payload, rendered markdown, manifest. Patterns: Authorization/Bearer headers, `sk-…`, `gh[opsur]_…`, AWS-style keys, basic-auth URLs, `password=`/`token=`, long hex.
- Preview before save, always.
- Choosing an Obsidian store triggers a one-time warning that vault folders may sync to cloud or git.

## Layout

```
learnings-keeper/
├── SKILL.md
└── bin/
    ├── store.py      # store config + Obsidian autodetect
    ├── extract.py    # candidate-notes extractor
    ├── save.py       # write a learning note (redacted)
    ├── lookup.py     # search past learnings
    └── redact.py     # vendored secret scrubber
```
