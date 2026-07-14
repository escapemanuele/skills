# prompt-to-command

Turns a prompt you keep retyping into a saved Claude Code **slash command**, so next time it's `/journal evening` instead of pasting a paragraph.

Part of the [skills-daimon plugin](../skills-daimon/): the daimon report *finds* the repeated prompt with no saved command (its clearest single win); this skill *fixes* it. It also works standalone — "save this as a command", "I keep typing this", "stop making me retype this".

## What it produces

A slash command is a markdown file — optional YAML frontmatter plus the prompt body:

```markdown
---
description: <one-line description, shown in the / menu>
argument-hint: "[morning|evening]"   # optional
---
<the generalized prompt body, with $ARGUMENTS or $1/$2 where things vary>
```

Saved to one of two scopes:

- **user** — `~/.claude/commands/<name>.md`, available in every repo (the default)
- **project** — `.claude/commands/<name>.md`, committed with the repo, shared with the team (chosen when the prompt is repo-specific)

## How it works

1. **Get the source prompt.** Verbatim from what you pasted or pointed at; or, if you only *named* a recurring task ("my evening journal prompt"), it scans your recent session history for the most-repeated prompts and lets you pick. It never guesses the wording — a command built from the wrong text is useless.
2. **Design the command.** Four decisions, asked only when genuinely ambiguous: the name (`/<name>`, kebab-case, checked for clashes with existing commands), the scope (user vs project), the arguments (the parts that change between runs become `$ARGUMENTS` or `$1`/`$2` placeholders; the stable instructions stay fixed text), and the one-line description.
3. **Write the file** — after confirming path and content with you, since it's creating a file in your config.
4. **Show usage** — `/name <example args>`, where the file lives, and that editing/deleting the file edits/removes the command.

## Guarantees

- **No secrets baked into command files.** If the original prompt contained a token or key, it's replaced with an argument or an env-var reference, and you're told.
- **`@file` references and tool mentions** from the original prompt are preserved.
- **Confirms before writing.** No silent file creation.

## Scope

A slash command is a single prompt template — it expands into a prompt Claude then acts on; it can't run code by itself. If the workflow needs bundled scripts or multiple files, that's a full skill, and this skill points you at `skill-creator` instead of overreaching.
