---
name: prompt-to-command
description: Turn a prompt the user keeps retyping into a saved Claude Code slash command. Use when the user says "save this as a command", "turn this into a slash command", "make a /command for this", "I keep typing this", "stop making me retype this", or when skills-daimon flags a recurring prompt with no saved command. Generalizes the prompt into a reusable template with arguments and writes it to ~/.claude/commands/.
allowed-tools: Read, Write, Bash, Glob, Grep, AskUserQuestion
---

# prompt-to-command — save a repeated prompt as a slash command

Takes a prompt the user types over and over and turns it into a reusable **slash command** so next time it's `/journal evening` instead of pasting a paragraph.

## When to use

- "Save this as a command" / "turn this into a /command".
- "I keep typing this" / "stop making me retype this every time".
- Following a **skills-daimon** report that flagged a *recurring prompt with no saved command* (the clearest single win it surfaces).

Don't use this to author a full multi-file skill — that's `skill-creator`. A slash command is a single prompt template. If the workflow needs bundled scripts or multiple files, point the user at `skill-creator` instead.

## Step 1 — Get the source prompt

Find the exact prompt to templatize, in this order:
1. **The user pasted it or pointed at a recent message** — use that verbatim.
2. **They named a recurring task** ("my evening journal prompt") — find it in their sessions:
   ```bash
   # Most-repeated recent user prompts (first line, with counts)
   for f in ~/.claude/projects/*/*.jsonl; do
     python3 - "$f" <<'PY'
   import json,sys
   for line in open(sys.argv[1]):
       try: ev=json.loads(line)
       except: continue
       if ev.get("type")=="user":
           c=ev.get("message",{}).get("content")
           t=c if isinstance(c,str) else " ".join(b.get("text","") for b in c if isinstance(b,dict) and b.get("type")=="text") if isinstance(c,list) else ""
           t=" ".join(t.split())
           if len(t)>40 and not t.startswith(("<","[Request","Caveat")): print(t[:200])
   PY
   done | sort | uniq -c | sort -rn | head -15
   ```
   Show the top candidates and let the user pick one. Then pull its full text from the session file.
3. **Neither** — ask the user to paste the prompt they want to save.

Never guess the prompt wording — a command built from the wrong text is useless.

## Step 2 — Design the command

Decide four things (ask the user only when genuinely ambiguous):

- **Name** — short kebab-case, becomes `/<name>`. Derive from the task ("evening journal" → `journal`). Check for a clash: `ls ~/.claude/commands/ .claude/commands/ 2>/dev/null`. If the name exists, pick another or confirm overwrite.
- **Scope** — **user** (`~/.claude/commands/<name>.md`, available everywhere) or **project** (`.claude/commands/<name>.md`, committed with the repo, shared with the team). Default to user unless the prompt is repo-specific.
- **Arguments** — find the parts that change between runs (a date, "evening"/"morning", a PR number) and replace them with placeholders:
  - `$ARGUMENTS` — everything the user types after the command, as one string.
  - `$1`, `$2`, … — individual positional args.
  Keep the stable instructions as fixed text. If nothing varies, the command takes no arguments.
- **Description** — one line, shown in the `/` menu.

## Step 3 — Write the command file

A slash command is a markdown file: optional YAML frontmatter + the prompt body.

```markdown
---
description: <one-line description>
argument-hint: <e.g. "[morning|evening]">   # optional, shown in the menu
---
<the generalized prompt body, with $ARGUMENTS or $1/$2 where things vary>
```

**Confirm the path and the content with the user before writing** (you're creating a file in their config). Then write it with the Write tool to the chosen path. Create the `commands/` dir if missing.

Rules:
- **Never bake secrets into the file** (tokens, keys). If the prompt contained one, replace it with an argument or an env-var reference and tell the user.
- Preserve any `@file` references and tool mentions from the original prompt.
- Frontmatter keys are optional; `description` is worth including.

## Step 4 — Confirm and show usage

Tell the user it's saved and how to run it:

> Saved `/<name>` → `<path>`. Use it like: `/<name> <example args>`.

If project-scoped, remind them it'll be committed with the repo. Mention they can edit the file anytime, and delete it to remove the command.

## Notes

- Slash commands are model-invoked prompt templates, not scripts. They can't run code by themselves — they expand into a prompt Claude then acts on.
- For a project-scoped command, the file belongs in the repo's `.claude/commands/`, not the user dir.
- This pairs with **skills-daimon**: that skill *finds* the repeated prompt; this one *fixes* it.
