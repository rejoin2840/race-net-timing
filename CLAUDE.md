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

## Model Selection Guidelines
- **Main plan**: Recommend which model (Opus/Sonnet/Haiku) with 1-line justification
- **Subagents**: Choose the most cost-efficient model that still accomplishes the task well
  - Haiku: Data processing, exploration, simple analysis
  - Sonnet: Complex reasoning, multi-step planning
  - Opus: Only if the main task demands maximum reasoning capability
- **Keep it brief**: One sentence explaining why—cost/capability trade-off in shorthand

## How I Like to Work
1. **Model pick**: Recommend model(s) + 1-line reason
2. **Plan (bullets)**: Concise bullet points—what, which subagents, their models
3. **Execute**: Make the changes
4. **Summary (brief)**: What changed, key learnings (1-2 sentences each)
5. **Concise**: Outside of what I requested you to explain or summarize, Output strictly tool calls and code. Zero conversational text, zero pleasantries, unless explicitly requested.

## Important Notes
- Real-time accuracy is critical (predictions must be reliable)
- Visual/UX feedback matters (dashboard needs to be readable under pressure)
- I'll ask "why did you choose X over Y?" frequently—embrace it
- Assume I want to understand the thinking, not just see results
