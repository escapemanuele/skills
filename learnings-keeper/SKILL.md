---
name: learnings-keeper
description: Save a short note of what was figured out in a session — bug found, decision made, library picked — as plain markdown to the user's Obsidian vault or a default folder, so the knowledge survives the session. Use when the user says "save what we learned", "remember this", "compound this", "what should I remember from this session?", or when skills-daimon flags a low memory-usage rate. Also looks up past learnings so prior solutions resurface when a similar problem comes up.
allowed-tools: Read, Write, Bash, Glob, Grep, AskUserQuestion
---

# learnings-keeper — save what you figured out, surface it later

The companion skill to **skills-daimon**. skills-daimon *diagnoses* (you don't save what you learn); this one *treats it* by capturing 1–3 short notes per useful session and writing them as plain markdown into a store the user already lives in (Obsidian vault) or a default folder. Later runs can **look those notes up** when a similar problem comes around — that's the compounding.

## When to use

- "Save what we learned" / "remember this" / "compound this".
- "What should I remember from this session?"
- skills-daimon's report flags a low memory-usage rate — the suggestion will name this skill by name.
- "Any past notes about X?" / "have we seen this before?" — the lookup mode.

Don't use this for a single quick note unrelated to a session — the value here is **distilling a real session into a portable lesson**. For one-off TODOs, the user can just edit their notes directly.

## The one rule

**Never write to disk without showing the user the draft first.** Every save passes through a preview → confirm → write flow. No silent saves. The redactor masks plausible secrets before the preview, but the user is the last line of defense.

---

## Step 0 — Configure the store (first run only)

If `~/.claude/skills/learnings-keeper/store.json` is missing, ask **one question**:

> *Do you want learnings saved into your Obsidian vault, or a default folder?*

Autodetect a vault first so the user just confirms:

```bash
python3 ~/.claude/skills/learnings-keeper/bin/store.py autodetect-vault
```

This prints `{"vault": "<path>", "suggested_subfolder": "...", "full_path": "..."}` if it finds a folder with `.obsidian/` under `~`, `~/Documents`, `~/Vault`, or `~/Obsidian` (up to two levels deep). If PARA-style folders are present (e.g. `3. Resources`), the suggested subfolder defaults to `<n>. Resources/Tech/Claude/Learnings/`, else `Claude/Learnings/`. The user may override the subfolder.

If the user picks **Obsidian**:

```bash
python3 ~/.claude/skills/learnings-keeper/bin/store.py set --kind obsidian --path "<vault>/<subfolder>"
```

If they pick **default**:

```bash
python3 ~/.claude/skills/learnings-keeper/bin/store.py set --kind default
```

Plugin path note: prefer `"$CLAUDE_PLUGIN_ROOT/bin/<script>"` when the skill is invoked from a plugin install.

**Warn once when Obsidian is chosen:** *"This folder may sync to the cloud or Git. The redactor catches obvious secrets, but please don't paste secrets into your Claude sessions."*

---

## Step 1 — Extract candidate notes from a session

```bash
python3 ~/.claude/skills/learnings-keeper/bin/extract.py --latest
```

(Or `--session <path>` for a specific jsonl.) Output schema:

```json
{ "session_id": "...", "repo": "...", "outcome": "...",
  "primary_success": "...",
  "candidates": [ { "text": "...", "source": "user|assistant" } ],
  "files_touched": ["..."],
  "tags": ["..."] }
```

Refusals (silent — return them to the user, don't save):
- `{"skipped": "outcome_negative", "outcome": "not_achieved"}` — the session didn't really achieve anything. Don't save unless the user **explicitly** insists (pass `--insist`). Failed sessions are not where compounding lives.

Candidates are pulled from sentences matching learn-phrases (English/Italian/Spanish/French): *"we decided"*, *"the fix was"*, *"turns out"*, *"figured out"*, *"capito"*, *"la soluzione era"*, etc. Already redacted before output.

If no candidates at all, **don't force a save**. Tell the user the session didn't surface anything distilled, and offer to do it manually if they want.

---

## Step 2 — Distill 1–3 notes (Claude does this)

You (Claude) read the candidates and any obvious context from the transcript tail, and **propose 1–3 short notes**. Each note has:

- `title` — a single sentence, ≤80 chars.
- `figured_out` — one or two short sentences. The thing that's worth remembering.
- `why_it_matters` — optional. One line of stakes.
- `try_next_time` — optional. One concrete instruction.

Keep tone close to how the user wrote it. **Quote real specifics** (file paths, error messages, library names) — the value of a learning is in being specific. The note's filename comes from a slug of the title.

Suggested tag list from extractor → use as-is, drop irrelevant ones, add up to two more from session context.

---

## Step 3 — Preview → confirm → save

Show the draft note(s) to the user, **inline in the chat, redacted**, framed like:

```
Save this to learnings? (yes / pick / no)

1. Yarn cache stale on PR 110697
   We figured out: The CI failure was a stale Yarn cache, not the new import.
   Why it matters: Wasted ~30 min twice this month on the same red herring.
   Try next time: When a lint job fails on an untyped import, clear yarn cache first.

2. ...
```

User responses:
- **yes** → save all proposed notes.
- **pick 1,3** → save only those.
- **no** → exit without writing anything.
- An edit like *"change 2's title to 'Yarn cache trap'"* → update the draft and re-show.

For each accepted note, send the payload to save.py:

```bash
echo '<payload>' | python3 ~/.claude/skills/learnings-keeper/bin/save.py
```

Payload schema:

```json
{
  "session_id": "<uuid>",
  "repo": "<short>",
  "outcome": "<facet outcome>",
  "tags": ["..."],
  "title": "...",
  "figured_out": "...",
  "why_it_matters": "...",
  "try_next_time": "...",
  "see_also": ["..."]
}
```

`save.py` writes `<store>/YYYY-MM-DD-<slug>.md` and appends an entry to `<store>/.skills-daimon.json` (the manifest the user can audit/delete from). Both the payload and the rendered markdown are passed through the redactor.

When `kind == "obsidian"`, the renderer adds a small `*See also:* [[name]]` line for any `see_also` provided. Otherwise omit.

**Confirm to the user after save**: filename + full path + (Obsidian only) *"Reading this in Obsidian shows it linked to its tags."*

---

## Step 4 — Lookup mode (the compounding part)

Two ways the lookup runs:

**User-initiated** — *"any past notes about yarn cache?"*, *"have we seen this CI thing before?"*:

```bash
python3 ~/.claude/skills/learnings-keeper/bin/lookup.py --query "yarn cache" --limit 5
```

Optional `--repo <name>` to scope to one repo. Output is a JSON list of `{path, title, date, repo, snippet}`. Show the user the top 3 with titles + dates + the matched snippet. Offer to open the file path if they want to read more.

**Auto-surfaced from skills-daimon** — skills-daimon's coaching can call this when it detects a recurring friction in a repo. Example: *"`wrong_approach` ×6 in wp-calypso this month. By the way, you wrote a note about something like this on 2026-04-12 — see [[2026-04-12-yarn-cache-stale]]."* That's how the loop actually compounds.

If `rg` (ripgrep) isn't installed on the machine, lookup falls back to a plain glob+grep — it's slower but never errors out.

---

## Privacy + scope discipline

- **Redactor runs at every disk boundary** — the payload, the rendered markdown, the manifest, and anything we write are passed through the same `bin/redact.py` patterns (Authorization/Bearer, `sk-…`, `gh[opsur]_…`, AWS-style, basic-auth URLs, password=/token=, long hex).
- **Preview before save, always.** No auto-write.
- **Subfolder only.** Never write to a vault root; the config saves the explicit `<vault>/<subfolder>` path and we mkdir under that.
- **MVP scope** — plain markdown only (Obsidian uses it natively; Logseq tolerates it). No Logseq-specific syntax, no "pick any folder" beyond the binary choice in Step 0. Per-repo playbooks are a separate later skill (PR δ), not this one.

## Failure modes

- **No facet file yet.** Anthropic writes facets a few minutes after a session ends. If `outcome` comes back as `unknown`, that's fine — extract anyway; we just don't have the label to gate on.
- **Empty candidates.** If extract returns zero, tell the user honestly. Don't fabricate a learning from nothing.
- **Secrets in the candidate text.** Show the redacted draft to the user. If they spot something the redactor missed, they can edit before save.
- **`rg` missing.** Lookup falls back automatically. Performance, not correctness.
- **Vault path moved.** If the configured path no longer exists, the next save tells the user and asks to reconfigure with `store.py set …`.
