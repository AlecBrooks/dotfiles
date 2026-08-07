#!/usr/bin/env bash
set -euo pipefail

TODAY="$(date +%Y-%m-%d)"
LOG="$HOME/.local/share/obsidian-journal.log"
mkdir -p "$(dirname "$LOG")"

PROMPT=$(cat <<EOF
Today's date is ${TODAY}. Write a short daily journal entry for my personal
Obsidian vault at ~/GitHub/Obsidian, summarizing what we worked on today.

Steps:
1. Check ~/CLAUDE.md for any content dated ${TODAY} across its project
   notes/log sections -- this is the primary log of dev work and the best
   source of what actually happened today.
2. For each of these repos, if it exists, run:
   git log --since="${TODAY} 00:00" --oneline --all
   ~/GitHub/craftnet
   ~/GitHub/craftnet-illuminations
   ~/GitHub/alecbrooks.github.io
   ~/dotfiles
   Note any commits made today.
3. Do NOT run any git command inside ~/GitHub/Obsidian itself, ever, for any
   reason -- that repo is synced via Obsidian's own Sync plugin, not git, and
   git must never be touched there.
4. Based on what you find, write a short, plain-language summary (a
   sentence or two to a short paragraph -- it does not need to be long).
   If you find no activity anywhere, just write one line saying nothing was
   worked on today -- that is a completely fine, correct outcome, do not pad
   it out or invent activity.
5. When the summary mentions a project by name, check
   ~/GitHub/Obsidian/Projects/*.md for a note whose title or content matches
   it (e.g. craftnet's page is titled "CNET 1" but its H1 heading is
   "CraftNet"), and if one exists, wikilink it with an alias matching the
   project's real name, e.g. [[CNET 1|craftnet]], instead of writing the
   project name as plain text. If no matching page exists yet, just use
   plain text -- do not invent a link to a page that doesn't exist.
6. Create the file ~/GitHub/Obsidian/Journal/${TODAY}.md (plain file write,
   not git) with this exact frontmatter, matching the vault's existing note
   convention, followed by the summary as plain prose:

---
date: ${TODAY}
time: 05:00 pm
type: Entry
tags:
  - journal
---

<summary here>

7. Only create that one new file. Do not edit anything else.
EOF
)

"$HOME/.local/bin/claude" -p "$PROMPT" \
  --permission-mode acceptEdits \
  --allowedTools "Bash Read Write Glob Grep" \
  --output-format text \
  >> "$LOG" 2>&1
