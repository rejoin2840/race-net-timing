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

### Session start — auto-branch off main
At the start of every session, check `git branch`. If on `main` and the session involves code changes, **automatically** create a feature branch (`git checkout -b feature/<short-name>`) before the first edit. Pick a sensible name from the task context and tell the user. Never make code changes directly on `main`.

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
grep -ri "kas""san" --include="*.md" --include="*.sh" --include="*.py" --include="*.plist" --include="*.json" --include="*.txt" -l .
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

### Doc-only changes — don't over-isolate
Small, incidental doc updates (a backlog note, a README tweak, a decisions-log entry) don't
need their own branch/PR — bundle them into whatever branch is already open for the related
work. Reserve a standalone doc branch/PR for changes that are substantial on their own, or
that need review separate from unrelated code sitting in another open PR (e.g. "is this fact
right" vs. "is this code correct").

The GitHub remote is `https://github.com/rejoin2840/race-net-timing`. Use `/opt/homebrew/bin/gh` if `gh` is not on PATH.

## Documentation versioning (single-source-of-truth rules)

Repeated doc-drift incidents ("X says awaiting review, it merged days ago") come from the
same fact living in multiple files. These rules end that:

1. **BACKLOG.md is the ONLY live status document.** Current state of any epic, decision,
   or open bug lives there and nowhere else. If another file needs to mention status, it
   links to BACKLOG.md instead of restating it.
2. **Every other .md is one of two kinds, declared in a banner on line 3:**
   - *Living reference* (README, USER_GUIDE, ARCHITECTURE, WEC_RACE_WEEK runbook,
     this file): kept current; must never contain epic/PR status.
   - *Frozen snapshot* (reviews, findings, direction/opinion docs): stamped
     `> Snapshot as of YYYY-MM-DD — current status: BACKLOG.md` and NEVER edited after
     the stamp. If reality diverges, the correction goes in BACKLOG.md, not the snapshot.
3. **A decision = one commit** containing the decisions-log entry AND every affected epic
   header/section in the same commit. Never log a decision without updating the sections
   it supersedes (including adding snapshot banners to docs it obsoletes).
4. **BACKLOG.md serialization: at most ONE open branch may touch BACKLOG.md.** If a
   branch touching it is already open, new BACKLOG changes go on that branch — and its
   PR title/body must be updated in the same session to cover the added scope. Never
   silently grow someone's PR.
5. **Docs branches merge same-day.** `docs/*` branches and any branch carrying a
   decisions-log entry get PR'd and merged (or explicitly handed to the owner for merge)
   in the session that created them. A stale docs branch is a sync bug by definition.
6. **Never commit onto a branch with uncommitted changes you didn't make.** Surface the
   dirty state to the owner; if work can't wait, use `git worktree add` for an isolated
   checkout instead of sharing the dirty one.
7. **Claude memory files point, never restate.** A memory entry about project status is
   one line + a pointer to BACKLOG.md / a PR / a commit hash. Multi-line status
   narratives in memory are how stale "awaiting review" claims survive.
8. **Session-end sync check (before ending any session that merged or committed):**
   BACKLOG.md status matches reality → PR body matches branch content → memory pointer
   updated → snapshots obsoleted by today's work got their banner. Four checks, ~1 min.

## Important Notes
- Real-time accuracy is critical (predictions must be reliable)
- Visual/UX feedback matters (dashboard needs to be readable under pressure)
- I'll ask "why did you choose X over Y?" frequently—embrace it
