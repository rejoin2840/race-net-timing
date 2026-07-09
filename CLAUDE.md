# Race Strategy Dashboard

## Preferences
- Show planning and reasoning before executing (keep it brief)
- Explain decisions in 1-2 sentences (non-technical when possible)
- Tell me which subagent/tool and why (concise)
- Optimize for accuracy over speed (this is for race strategy, precision matters)
- **Save tokens**: Use bullet points, short sentences, avoid verbose explanations

## Subagents & Tool Usage (IMPORTANT)
- **Default behavior**: Use subagents and Claude tools to improve efficiency and output quality
- **When to delegate**: Break complex tasks into specialized subagents (exploration, analysis, implementation, testing)
- **Tell me your reasoning**: Explain which subagent you're using, why it's appropriate, and what model you chose for it
- **Token efficiency**: Use cheaper models (Haiku, Sonnet) for subagents; reserve expensive models for tasks requiring deep reasoning
- **Before you start**: Recommend a model for the main plan and explain why, then recommend models for any subagents

## Model Selection Guidelines (IMPORTANT)
- **Main plan**: Recommend which model (Opus/Sonnet/Haiku) with 1-line justification
- **Subagents**: Choose the most cost-efficient model that still accomplishes the task well
  - Haiku: Data processing, exploration, simple analysis
  - Sonnet: Complex reasoning, multi-step planning
  - Opus: Only if the main task demands maximum reasoning capability
- **Keep it brief**: One sentence explaining why—cost/capability trade-off in shorthand

## How I Like to Work
1. **Model pick**: Recommend model(s) + 1-line reason - prompt me to change models at appropriate points of the plan being worked 
2. **Plan (bullets)**: Concise bullet points—what, which subagents, their models
3. **Execute**: Make the changes
4. **Summary (brief)**: What changed, key learnings (1-2 sentences each)
5. **Concise**: Outside of what I requested you to explain or summarize, Output strictly tool calls and code. Zero conversational text, zero pleasantries, unless explicitly requested.

## Git Workflow

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

### After every commit — push to GitHub
```bash
git push origin feature/endurance-refocus
```

### When merging to main (stable share branch)
```bash
git checkout main
git merge feature/endurance-refocus --no-ff -m "<message>"
git push origin main
git checkout feature/endurance-refocus
```

Always push both branches after a merge. The GitHub remote is `https://github.com/rejoin2840/race-net-timing`. Use `/opt/homebrew/bin/gh` if `gh` is not on PATH.

## Important Notes
- Real-time accuracy is critical (predictions must be reliable)
- Visual/UX feedback matters (dashboard needs to be readable under pressure)
- I'll ask "why did you choose X over Y?" frequently—embrace it
