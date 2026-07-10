# Race Strategy Dashboard

## Preferences
- Show planning and reasoning before executing (keep it brief)
- Explain decisions in 1-2 sentences (non-technical when possible)
- Tell me which subagent/tool and why (concise)
- Optimize for accuracy over speed (this is for race strategy, precision matters)
- **Save tokens**: Use bullet points, short sentences, avoid verbose explanations

## Subagents & Tool Usage
- **Scale to the task**: for a quick lookup, small edit, or question, just do it directly — don't spin up a subagent or narrate tool choice for routine stuff. Reserve subagents for genuinely complex/parallel/exploratory work (multi-file investigation, independent research threads, big refactors).
- **When you do delegate**: tell me which subagent and which model, 1-line reason why.
- **Token efficiency**: cheaper models (Haiku, Sonnet) for subagent grunt work; reserve expensive models for tasks that need deep reasoning.

## Model Selection Guidelines (IMPORTANT — keep this one)
- **Prompt me to switch models at phase boundaries** (e.g. moving from planning to implementation, or into a token-heavy exploration phase) — 1-line reason why a different model fits better. This is the one piece of ritual I want kept even for small tasks.
- **Subagents**: cheapest model that still gets the task done (Haiku for data/exploration, Sonnet for reasoning/planning, Opus only if the main task truly demands it).

## How I Like to Work
- **Simple asks** (lookups, small fixes, questions): just answer/do it. Skip the plan-bullets ritual — but still explain your reasoning/decisions in 1-2 sentences, since I'm learning from this.
- **Non-trivial tasks** (new features, multi-step changes, anything touching the live pipeline): give a brief plan (bullets: what, which subagents/models if any) before executing, then a short summary after (what changed, key learnings).
- **Always**: explain decisions in 1-2 sentences, non-technical where possible — I'm using these explanations to learn, don't drop them for terseness.
- Outside of explanations I've asked for, keep responses concise — no filler or pleasantries.

## Git Workflow

### Before every commit — run the fast test gate
```bash
./check.sh
```
~142 tests, ~5s. Required before every commit.

### Before merging to main, or after touching core calc logic (net position/predictor/evaluator)
```bash
./check.sh --full
```
Adds the 6-race regression suite — slower, so save it for merges or changes to the actual math, not routine commits.

### Before every commit — anonymization check (REQUIRED)
This is a public repo. Run both scans before staging anything:
```bash
grep -ri "kassan" --include="*.md" --include="*.sh" --include="*.py" --include="*.plist" --include="*.json" --include="*.txt" -l .
grep -rn "\bPaul\b" --include="*.md" --include="*.sh" --include="*.py" --include="*.json" --include="*.txt" . | grep -v "São Paulo\|Paul Miller\|Paul-Loup\|Paul Di Resta"
```
Both must return nothing. Also check any new file for:
- Hardcoded `/Users/<name>/...` paths — replace with relative paths or `~`
- Personal email addresses
- Real names (first or last) in comments, docstrings, decisions logs, or config values

The repo author identity is `rejoin2840` — no real name should appear anywhere in tracked files or commit metadata. Repo-local git config is already set to `rejoin2840 <rejoin2840@users.noreply.github.com>`.

### Branch lifecycle (always follow this pattern)
1. **Start new work** — branch off main:
   ```bash
   git checkout main && git pull origin main
   git checkout -b feature/<short-name>
   ```
2. **Work & commit** on the feature branch. Push after every commit:
   ```bash
   git push -u origin feature/<short-name>
   ```
3. **Merge to main** — open a PR via `gh pr create`, merge via `gh pr merge`, then clean up:
   ```bash
   git checkout main && git pull origin main
   git branch -d feature/<short-name>
   git push origin --delete feature/<short-name>
   ```

`main` is always the stable, shareable version. Never commit directly to `main` — always branch first. Merge when you want `main` to reflect "what a visitor should see right now" (before sharing, before a checkpoint, before a race — not on a timer).

The GitHub remote is `https://github.com/rejoin2840/race-net-timing`. Use `/opt/homebrew/bin/gh` if `gh` is not on PATH.

## Important Notes
- Real-time accuracy is critical (predictions must be reliable)
- Visual/UX feedback matters (dashboard needs to be readable under pressure)
- I'll ask "why did you choose X over Y?" frequently—embrace it
