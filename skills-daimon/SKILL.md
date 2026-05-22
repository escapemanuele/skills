---
name: skills-daimon
description: Look at the user's recent Claude Code sessions, spot recurring jobs in the actual work, recommend matching skills/plugins from every reachable catalog, AND coach better habits (native tools over raw shell, safer git, CLAUDE.md, saved commands) from the same evidence. Use when the user asks "what skills should I install?", "what could I be doing better in Claude?", "find skills that match my work", "how can I improve my Claude usage?", or otherwise wants evidence-based recommendations and coaching grounded in their own usage.
allowed-tools: Bash, Read
---

# skills-daimon — evidence-based skill recommendations and coaching

Looks at the user's recent Claude Code sessions, identifies recurring jobs they keep doing by hand, recommends skills/plugins from the catalogs the user can reach, and coaches better working habits — all grounded in hard counts from their own sessions. Every recommendation must come from a catalog the scanner found (never invent skills), and every coaching point must cite a real count (never generic advice).

## When to use

Run this skill when the user asks any of:
- "What skills should I install?"
- "Find skills for the things I do."
- "What could I do better with Claude?"
- "Analyze my Claude usage and recommend tools."
- Anything mentioning "skills-daimon" by name.

Skip if the user asks for general advice on a specific task — that's not what this skill does. This skill is **about the user's past work, not a current task.**

## The one rule

**Never invent a skill or plugin.** Every recommendation in the output must come from a real entry returned by one of the catalogs the scanner discovered. If nothing matches a job, say so explicitly — do not guess a plausible-sounding name.

## Step 1 — Scan the user's sessions (hard counts, no LLM)

Run the bundled scanner. It reads `~/.claude/projects/*/*.jsonl`, filters to the last 4 weeks (28 days) by default, and emits a single JSON blob with deterministic counts:

```bash
python3 ~/.claude/skills/skills-daimon/bin/scan.py --days 28
```

(If invoked as a plugin and `$CLAUDE_PLUGIN_ROOT` is set, prefer `"$CLAUDE_PLUGIN_ROOT/bin/scan.py"`.)

The JSON includes:
- **Evidence fields:** `session_count`, `projects`, `tool_use_top`, `mcp_calls_top`, `bash_verbs_top`, `bash_verb_samples`, `web_fetches`, `recurring_prompts`, `sampled_oneoff_prompts`, `session_index`. Use these as evidence in the report — every claim about how many times the user did X must come from this JSON.
- **`bash_verb_samples`** maps a verb to up to 5 real command lines the user ran. Use it whenever a top verb is ambiguous (`node`, `curl`, `python3`, `for`, `cd`) — the samples reveal the actual workflow. Quote them in the Evidence line when they sharpen the job tag.
- **Filter fields:** `installed_skills` (list of skill `name:` values found locally), `installed_plugins` (list of plugin names from `installed_plugins.json`), `ignored_names` (user-dismissed names from `~/.claude/skills/skills-daimon/.ignored.json`). Use these to **skip recommending anything already installed or explicitly dismissed**. If a search result's `name` or `slug` matches anything in `installed_skills`, `installed_plugins`, or `ignored_names`, drop it.
- **Catalog fields:** `available_catalogs` (list of catalog sources reachable in this env). Each entry has `name` and `type` — one of `marketplace`, `cli-provider`, `cli-registry`, or `mcp-server` — plus a type-specific field: `marketplace_json` (local JSON index), `tool` (CLI command like `wp context <provider>` or `npx skills find`), or `probe` (instructions for an `mcp-server`). The `cli-registry` entry (skills.sh) also carries `install_tool` (`npx skills add`) and `init_tool` (`npx skills init`). Query every catalog in the list — never hardcode one. **`mcp-server` entries are catalog *candidates*** the scanner found in config but could not probe (a subprocess can't call MCP) — you must probe them yourself, see Step 3.
- **Coaching fields:** `coaching_signals` — deterministic habit signals for the teacher section (Step 5). Sub-fields:
  - `native_tool_bypass` — `{bash_total, bypass_calls, bypass_total, suggested_tool, native_tool_use}`. `bypass_calls` counts shell verbs (`grep`/`find`/`cat`/`head`/`tail`/`sed`/`awk`) that duplicate a native Claude tool; `suggested_tool` maps each to its replacement; `native_tool_use` shows how often Grep/Glob/Read were used instead. Compare the two.
  - `destructive_cmds` — list of `{label, count, sample}` for risky commands seen (`git push --force`, `git reset --hard`, `git clean -fd`, `--no-verify`, `rm -rf`). The sample is one real command.
  - `raw_http_hosts` — `{host: count}` for hosts hit by raw `curl`/`wget`. A host with a dedicated CLI/MCP (e.g. `teamcity.a8c.com`) is a coaching opportunity.
  - `sleep_calls` — count of foreground `sleep` calls (often polling that a proper wait/background job would replace).
  - `hot_repos_without_claudemd` — `[{path, sessions}]` for git repos with ≥3 sessions and no `CLAUDE.md` (context re-explained each session).
- **Usage field:** `usage` — token totals and approximate cost over the window. `{pricing_as_of, days, totals:{input,output,cache_write,cache_read}, by_model:{<model>:{...tokens, est_cost_usd}}, est_cost_usd_total, uncosted_models}`. Cost is computed from a hardcoded per-model price table — **always present it as approximate and cite `pricing_as_of`.** `cache_read` tokens usually dwarf raw input (that's normal and cheap); don't alarm the user about raw token counts without the cache context.
- **Work-recap field:** `work_recap` — `{top_projects:[{path, sessions, tokens, kind, branch}], mix:{dev,writing,data,ops}}`. `kind` is the dominant signal per project (dev / writing / data / ops); `mix` is the rough percentage split across the window. Use this to describe **what** the user works on and to **weight which recommendations surface first** (see Step 2).

If `session_count` is 0, stop and tell the user there's no recent session data to analyze.

### Dismissing a recommendation

If the user says "I don't want <name> recommended again" (or similar), append the name to the ignored list and confirm:

```bash
python3 ~/.claude/skills/skills-daimon/bin/scan.py --ignore <name>
```

The next scan will skip it automatically. Reverse with `--unignore <name>`. List with `--list-ignored`.

## Step 2 — Cluster the signals into 3–5 recurring jobs

Read the JSON. Identify 3–5 distinct "jobs" the user keeps doing. Lean on:

- **Bash verbs** (`gh pr view` × 14, `git log` × 43, `wp context` × 36) — strong signal of a repeated workflow.
- **Tool-use mix** (heavy WebFetch + Read = browser-fetch-and-read workflow; heavy mcp__linear = Linear-driven work).
- **Recurring prompts** — same question asked across sessions usually points to a repeated task.
- **MCP calls** — what the user already uses today (do not recommend skills they already have working).

For each job, write a short tag (e.g. *"PR review by hand"*, *"Linear triage"*, *"Codebase grep tours"*) and **the concrete evidence count** that justifies it.

Aim for high-signal jobs only. If you can't tie a "job" to specific counts from the JSON, drop it.

**Weight jobs by `work_recap`.** The area where the user spends the most time/tokens should bias which jobs (and therefore recommendations) surface first. If `work_recap.mix` is, say, 60% dev / 33% writing, lead with dev jobs but don't ignore the writing third. If the top projects are non-dev (journal, ops, data), the recommendations should serve *that* work — skills-daimon is not a dev-only tool.

## Step 3 — For each job, query every available catalog

Loop over **every entry** in `available_catalogs` from the scan JSON. Different catalogs need different lookup tools, but the matching logic is the same. Query each with the same short noun-phrases derived from the job tag (e.g. *"pull request review"*, *"linear issue triage"*) — these are substring matches behind the scenes, not full sentences.

### Per-catalog lookup

**Marketplace catalogs** (`type: "marketplace"`):
- Read the JSON file at `marketplace_json` — it has top-level `plugins: [...]` where each item carries `name`, `description`, and component metadata.
- Match by case-insensitive substring against `name`, `description`, and any `keywords` field.
- Install command is always derivable: `/plugin install <name>@<marketplace-name>`.

**CLI-provider catalogs** (`type: "cli-provider"`, e.g. `tool: "wp context <provider>"`):
- Search: `<tool> search query=<term> limit=10`. Use the `tool` field from the catalog entry verbatim.
- These catalogs typically aggregate from many upstream repos and return entries with `name`, `slug`, `type`, `description`, `repo_key`, `source_url`.

**CLI-registry catalog** (`type: "cli-registry"`, the public skills.sh registry, `tool: "npx skills find"`):
- Search: `npx skills find <term>` (the `tool` field verbatim, then the noun-phrase). This is the largest catalog — query it for every job.
- Results carry the skill name, owner/repo, a description, **install count**, and **GitHub stars** of the source repo. Capture those numbers — they drive the quality ranking in the "Combine results" step below.
- First call in a session may be slow (npx fetches the package); subsequent calls are fast.

**MCP-server catalog candidates** (`type: "mcp-server"`): the scanner found this MCP server configured but **could not probe it** — only you, the live session, have MCP access. For each one:
1. **Enumerate it.** Some MCP servers front many providers (e.g. context-a8c → `load-provider` lists `slack`, `linear`, `ai-skills`, …); others expose tools directly. List the providers/tools.
2. **Keep only catalog providers.** A provider is a catalog if it exposes a `search`+`get` pair whose `search` description is catalog-flavored (mentions skill / plugin / agent / marketplace / directory / catalog). Drop everything else (a Slack search or Linear search is not a skills catalog). Example: context-a8c's **`ai-skills`** provider = the *Automattic Agent Skills Directory* — a real catalog. This is **blessed** (the user's own org), so it outranks public sources on ties.
3. **Query it** with the same per-job noun-phrases: call the server's search tool (e.g. context-a8c `execute-tool` on `ai-skills` `search` with `query=<term>`), then `get` (with the returned `slug`+`repo_key`) for the final picks. Results carry `name`, `slug`, `type`, `description`, `repo_key`, `source_url` — same shape as a cli-provider.
- If the server can't be reached or has no catalog provider, skip it silently — it was only a candidate.

If `available_catalogs` is empty, stop and tell the user: *"No skill catalogs reachable. Add at least one marketplace (e.g. `/plugin marketplace add anthropics/claude-plugins-official`) or install Node so `npx skills` works, then try again."*

### Combine results across catalogs

For each job, merge candidates from every catalog:
- **Dedupe by name** — if the same plugin or skill shows up in more than one catalog, keep one entry. Prefer the skills.sh entry when present (it carries install counts + stars for ranking), then the CLI-provider version (richer descriptions, `get` returns the real SKILL.md), then the marketplace entry.
- **Pick ONE winner per job** using these priorities:
  1. Drop anything already installed (cross-check `installed_skills` and `installed_plugins`) or in `ignored_names`.
  2. Entries that explicitly describe the job (don't settle for "kinda fits").
  3. **Use hard popularity numbers as the tie-breaker, when the catalog reports them** (skills.sh does):
     - **Install count:** 1K+ installs = solid signal, recommend with confidence. Under 100 installs = treat with caution and say so in the Evidence line. Prefer the higher-install entry between two comparable matches.
     - **GitHub stars of the source repo:** a source repo with under 100 stars should be treated with skepticism — drop the confidence dot or flag it.
     - **Official / blessed sources** (`anthropics`, `vercel-labs`, the upstream tool vendor, or the user's own org) outrank unknown personal authors.
  4. If the catalog reports **no** install/star numbers (e.g. local marketplaces, cli-providers), fall back to: blessed source > richer description > newer entry. **Do not penalize an entry for lacking install counts** — only compare numbers between entries that both have them.

If nothing clearly matches a job, it goes to the **gaps section** — don't force a recommendation.

**Use the right label.** Each catalog entry has a `type` field — `skill`, `plugin`, `agent`, `command`, etc. The label in the heading must match: write **"Recommended plugin:"** for plugins (which bundle multiple skills), **"Recommended skill:"** for standalone skills, **"Recommended agent:"** for agents, and so on. Never call a plugin a skill — they're different units with different install commands and surface areas.

## Step 3.5 — Fetch full details for the final picks

For each recommendation, fetch its full content so the install command and trigger phrases are real, not paraphrased:

- **CLI-provider hit:** `<tool> get slug=<slug> repo_key=<repo_key>` (using the `tool` field from the catalog entry) — returns the full SKILL.md.
- **skills.sh hit:** the `npx skills find` result already carries name, description, install count, and stars. That's enough to recommend; no extra fetch needed. If you want the full SKILL.md before recommending, the source is `https://www.skills.sh/<owner>/<repo>/<skill>`.
- **Marketplace hit:** read the entry directly from the `marketplace_json` file (already in memory from Step 3). If the plugin is cached locally at `~/.claude/plugins/marketplaces/<mp>/<plugin>/`, also read its `README.md` or the bundled `SKILL.md` for richer detail.

The install command rules:
- **skills.sh registry hit** (came from `npx skills find`): `npx skills add <owner/repo>@<skill> -g -y`. `-g` installs at user level, `-y` skips the confirm prompt. This is the preferred recipe for any skills.sh result — don't fall back to manual clone for these.
- **Plugin in a marketplace:** `/plugin install <name>@<marketplace>`. If the marketplace isn't yet added on the user's machine, prepend `/plugin marketplace add <source>` (the source comes from `known_marketplaces.json` or, for unknown ones, from the entry's `repo_key`).
- **Standalone skill (no skills.sh entry):** clone the repo + copy the skill dir into `~/.claude/skills/<name>/`.

If `get` fails for a CLI-provider pick, fall back to the search description and flag the install as `Install: see source_url` so the user knows it's unverified.

## Step 4 — Output the report

Use this exact template. Keep it scannable. Be specific. Be honest.

```
# Your skill fit — last 4 weeks

📊 **Visual report:** <file:// URL printed by render_report.py> *(open in a browser)*

Scanned **N sessions** across **M projects**.
Catalogs queried: <comma-separated list of `available_catalogs[*].name`>.

## What you've been working on

<One-line read of `work_recap.mix`, e.g. "Mostly dev (61%) with a big writing streak (33%) — some data and ops.">

| Project | Sessions | Focus |
|---|---|---|
| <basename of path> | <sessions> | <kind> |

(Top 3–5 from `work_recap.top_projects`. Use the path basename, not the full path. This frames the rest of the report — the recommendations below serve this work.)

## Usage & cost (last 4 weeks)

**<totals.input + cache>** in · **<totals.output>** out · **~$<est_cost_usd_total>** *(approximate, priced <pricing_as_of>)*

<If more than one model in `by_model`, one line naming the heaviest, e.g. "Mostly claude-opus-4-7.">. Note `cache_read` tokens are large and cheap — normal, not a problem.

## TL;DR — recommendations

| Type | Name | Matches | Why |
|---|---|---|---|
| <type> | [<name>](<source_url>) | <job tag> | <≤10-word reason, e.g. "wraps `wp context` you ran 36×"> |
| <type> | [<name>](<source_url>) | <job tag> | <≤10-word reason> |
| <type> | [<name>](<source_url>) | <job tag> | <≤10-word reason> |

(One row per recommendation, in the same order as the detailed sections below. Skip the table entirely if there are zero recommendations. The table is the ONLY place inline markdown links appear — body text uses bold names only.)

### 1. ●●● <job tag>
**Evidence:** <one-sentence confidence rationale leading the line>. <Then the supporting counts from the JSON: e.g. "ran `gh pr view` 14 times, `gh pr diff` 9 times across 6 sessions">.
**Recommended <type>:** **<name>** — <one-line description from the get response (richer than search)>
**Install:**
- `<exact command 1, in backticks>`
- `<exact command 2, in backticks>`
- <or a short prose step if there's no command, still as a bullet>

### 2. ●●○ <job tag>
... same shape ...

## ○○○ Gaps — workflows worth authoring

- **<job tag>** — <one-line: evidence + why no catalog match>. Start one: `npx skills init <name>`
- **<job tag>** — <one-line: evidence + why no catalog match>. Start one: `npx skills init <name>`

## ⚑ Habits & leverage — coaching

### <habit tag>
**What we saw:** <hard count, plainly, e.g. "You ran `grep`/`find`/`cat` in the shell 214 times. The built-in search tools did the same job 864 times.">.
**Why it matters:** <one short, plain sentence — no jargon. e.g. "Shell search is slower and gives messier results than the built-in tools.">.
**Try this:** <one concrete fix, said simply. e.g. "Reach for the built-in search/read first; keep shell `grep` for real pipes.">.
```

**Gaps** are in their own list below the numbered recommendations. Each is a one-line bullet (no Evidence/Install/Confidence blocks) ending with a `npx skills init <name>` scaffold command so the gap is actionable, not just named. The `○○○` lives in the section heading, not on each bullet, so the eye scans the list as a clean group. Only append the `npx skills init` command when skills.sh is in `available_catalogs` (i.e. `npx` is present); otherwise leave the bullet as a plain description.

### Links: table only

The user's terminal renders inline markdown links as `name (url)` — the URL is appended visibly. That's an unavoidable CLI-renderer behavior, so:

- **Only the TL;DR table uses `[name](url)` markdown links.** The link is still clickable; the visible URL alongside is the cost.
- **Body prose uses bold names only** — `**<name>**`, not `[<name>](url)`. Never put a URL in the detailed sections, the gaps section, or the install line.
- **No links/references section at the end.** The user already has the URLs in the table.

### Confidence indicator in section headers

Numbered recommendations carry a filled-dot signal in the heading:

- `●●●` — high confidence
- `●●○` — medium confidence
- `●○○` — low confidence

The dots go between the number and the job tag: `### 1. ●●● <job tag>`. There is **no separate "Confidence:" line** — the dots are the confidence level, and the reasoning behind that level should be the **opening sentence of the Evidence line** (e.g. *"Evidence: You literally tried to install it last week — gh pr ×30, ..."*). This keeps a single source of truth for confidence.

Gap entries use `○○○` in their **section heading** (`## ○○○ Gaps — workflows worth authoring`), not on each bullet. Each gap is a one-line bullet with the job tag bold-leading and evidence inline. No Install block.

## Step 5 — Coaching: teach better habits from the same evidence

skills-daimon is also a **teacher**, not just an installer. After the recommendations and gaps, read `coaching_signals` and surface a few high-signal habits the user could improve. The bar is the same as for recommendations: **every point cites a hard count from the JSON** — no vibes, no generic best-practice lecture.

Build coaching points from these signals (skip any that don't clear the threshold):

**Anti-patterns (doing it the hard way):**
- **Native-tool bypass** — if `native_tool_bypass.bypass_total` is a meaningful share of `bash_total` (rule of thumb: ≥10% AND ≥30 calls), point it out. Quote the breakdown (`grep ×N`, `find ×N`, `cat ×N`) and the `suggested_tool` mapping (Grep/Glob/Read), and contrast with `native_tool_use`. The fix: prefer the native tools — they're structured, cheaper, and don't spawn a shell.
- **Raw HTTP to a tool-backed host** — for each entry in `raw_http_hosts`, if the host has a known CLI/MCP (e.g. `teamcity.a8c.com` → `teamcity` CLI; an API you also see in `available_catalogs`), flag it. Don't flag localhost or one-off hosts.
- **Destructive commands** — if `destructive_cmds` is non-empty, surface the riskiest (`git push --force`, `git reset --hard`, `--no-verify`) with the count and the safer alternative (`--force-with-lease`, `git revert`, fix the hook). Skip `rm -rf` against `/tmp` — that's normal cleanup, not a habit worth flagging.
- **Sleep-polling** — if `sleep_calls` ≥ 5, suggest a proper wait/background job over fixed `sleep`.

**Missing patterns (leverage not used):**
- **Recurring prompt with no saved command** — cross-reference `recurring_prompts` (count ≥ 3) against `installed_skills`. A high-count prompt with no matching skill/command means the user retypes the same instruction — recommend turning it into a `/slash-command` or a skill (`npx skills init <name>` if skills.sh is reachable). The clearest single win for most users.
- **Hot repo without CLAUDE.md** — for each entry in `hot_repos_without_claudemd`, suggest adding a `CLAUDE.md` so per-repo context stops being re-explained every session.
- **Plan mode / subagents / memory** — only mention these if a signal supports it (e.g. very large multi-file edit sessions → plan mode; many independent parallel tasks → subagents). Don't list them generically.

**Delivery rules:**
- **Cap at 3 coaching points.** Pick the highest-signal ones. A short, sharp coaching section beats an exhaustive nag.
- Each point uses the **What we saw → Why it matters → Try this** shape from the template. Lead with the count.
- **Write plainly.** Short sentences, everyday words. Explain any tech term in passing or avoid it. Aim for a smart friend, not a manual: "Shell search is slower and messier" beats "spawns a subprocess returning unstructured stdout."
- **Frame as a lever, not a scolding.** "Most of your searching already uses the fast tools — here's the last bit" — not "you're using grep wrong."
- If no signal clears its threshold, **omit the whole section.** Silence is better than manufactured advice.

### Tone

- Lead with evidence. Every recommendation AND every coaching point must cite a count.
- Mark confidence honestly — *"low"* is fine and trustworthy.
- No more than 5 recommended skills total. No more than 3 gap entries. No more than 3 coaching points.
- **Evidence-bound coaching is in scope** — calling out a habit ("214 raw `grep`/`find`/`cat` calls — the native Grep/Glob/Read tools are faster") is exactly the teacher role, *as long as it cites a count*. What's still out of bounds is **count-free editorializing** ("you should review PRs less often") — opinions with no number behind them.

## Step 6 — Render the HTML companion

After the markdown report is written, generate a self-contained visual version and link it at the top.

1. **Assemble a payload JSON** from the analysis you just produced plus the deterministic counts from the scan. Schema (see `bin/render_report.py` docstring for the authoritative version):
   - `meta`: `{days, sessions, projects, date, catalogs}` (date = today, ISO).
   - `recommendations`: one object per rec — `{rank, confidence: "high"|"med"|"low", type, name, job, evidence, description, install: [cmds], source_url}`.
   - `gaps`: `[{tag, note, init}]`.
   - `coaching`: `[{title, evidence, costs, better}]`.
   - `charts`: lift straight from the scan JSON — `tool_use_top`, `bash_verbs_top`, and `native_bypass: {bypass_total, native_total: <Grep+Glob+Read>, bypass_calls}` from `coaching_signals.native_tool_bypass`.
2. **Write the payload** to a temp file and run the renderer:
   ```bash
   python3 ~/.claude/skills/skills-daimon/bin/render_report.py /tmp/skills-daimon-payload.json
   ```
   (If invoked as a plugin, prefer `"$CLAUDE_PLUGIN_ROOT/bin/render_report.py"`.) It writes `~/.claude/skills/skills-daimon/reports/skills-daimon-<date>.html` and prints `{"path","url"}` as JSON.
3. **Link it at the very top** of the markdown report using the printed `url` (a `file://` URL the user opens in a browser). If rendering fails for any reason, skip the link silently — the markdown report stands on its own.

The HTML is fully self-contained (inline CSS + inline SVG charts, no network) so it opens offline and nothing leaves the machine.

## Failure modes to watch for

- **Hallucinating skills**: if you find yourself recommending a skill that wasn't in a `search` result, stop — that's the failure case the one rule prevents.
- **Recommending what they already use**: cross-check `mcp_calls_top` and `tool_use_top` before recommending a skill that wraps something they already invoke directly.
- **Counting noise as signal**: very-low-volume bash verbs (1–2 calls) usually aren't jobs. Require at least 3 occurrences across at least 2 sessions to count something as recurring.
- **Going wide instead of deep**: 3 well-grounded recommendations beat 8 hand-wavy ones.
