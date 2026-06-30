# Race Strategy Dashboard

## About Me
- Product management + IT background (not a software engineer)
- Learning why AI makes decisions—explain the reasoning
- Want to understand trade-offs and design choices
- Tinkering hobbyist with technical depth

## My Preferences
- Show planning and reasoning before executing changes
- Explain decisions in non-technical terms when possible
- Tell me when you're using a subagent or tool and why
- Optimize for accuracy over speed (this is for race strategy, precision matters)

## Subagents & Tool Usage (IMPORTANT)
- **Default behavior**: Use subagents and Claude tools to improve efficiency and output quality
- **When to delegate**: Break complex tasks into specialized subagents (exploration, analysis, implementation, testing)
- **Tell me your reasoning**: Explain which subagent you're using, why it's appropriate, and what model you chose for it
- **Token efficiency**: Use cheaper models (Haiku, Sonnet) for subagents; reserve expensive models for tasks requiring deep reasoning
- **Before you start**: Recommend a model for the main plan and explain why, then recommend models for any subagents

## Model Selection Guidelines
- **Main plan**: Tell me which Claude model you recommend (Opus/Sonnet/Haiku) and justify it based on task complexity
- **Subagents**: Choose the most cost-efficient model that still accomplishes the task well
  - Haiku: Data processing, exploration, simple analysis
  - Sonnet: Complex reasoning, multi-step planning
  - Opus: Only if the main task demands maximum reasoning capability
- **Your reasoning**: Explain the cost/capability trade-off for each model choice

## How I Like to Work
1. **Model recommendation**: Tell me which model(s) you'll use and why
2. **Explain the plan**: Show what you're doing, which subagents you'll delegate to, and their models
3. **Execute**: Make the changes
4. **Summarize**: Tell me what changed, what we learned, and why this approach was efficient

## Important Notes
- Real-time accuracy is critical (predictions must be reliable)
- Visual/UX feedback matters (dashboard needs to be readable under pressure)
- I'll ask "why did you choose X over Y?" frequently—embrace it
- Assume I want to understand the thinking, not just see results
