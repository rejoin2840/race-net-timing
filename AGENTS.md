# Overcut project instructions

> Living reference — current project status: [BACKLOG.md](BACKLOG.md).

## Working agreements
- Global collaboration preferences apply. Give short plans for substantial work and explain decisions in one or two sentences.
- At major phase boundaries recommend keeping/switching the selected model; do not change it automatically.
- Accuracy and readable confidence signals matter more than feature count. Keep the Python/SQLite engine boundary and the approved Electron display direction.
- Preserve the product decisions in BACKLOG.md: solo-first, race-day use, one calm board, dense timing escape hatch, retrospective WYWA without NET projections, video parked. Practice/quali validate data only.
- Read BACKLOG.md before changes. Reopen prior decisions only with new evidence; preserve completed milestones and record corrective follow-ups explicitly.

## Git and verification
- Check status/branch first. Never edit on main. Preserve changes made by others; use an isolated checkout if needed.
- Commit, push, PR, and merge only when the user's request authorizes those steps. This overrides CLAUDE.md's automatic-push wording for Codex.
- Before any commit run ./check.sh. Calculator/replay/evaluator changes require ./check.sh --full; WEC changes also require the WEC suite; finish changes require all races.
- UI changes require typecheck, behavior tests, production build, an offscreen render, and a streaming replay feel-test. Live acceptance stays pending until a real event.
- A baseline refresh is an explicit action, never an automatic response to a failing regression. Do not tune off a single race or practice.
- Before staging, scan tracked/new text for personal identities, personal email, hardcoded user paths, and credentials. Racing names such as São Paulo and Paul Miller are legitimate. Preserve repo-local pseudonymous Git identity rejoin2840.

## Documentation
- BACKLOG.md is the only live status document. Other documents link to it rather than restating epic or PR status.
- Living references carry a line-3 banner. Frozen snapshots carry their date and a BACKLOG.md pointer; do not rewrite historical findings.
- Keep decisions and every affected epic section together. One active branch owns BACKLOG.md edits; do not silently expand another PR's scope.
- Incidental docs accompany their related code. At session end check backlog accuracy, any PR description, and pointers to superseded evidence.
- Keep CLAUDE.md intact. Its project conventions remain useful; this file governs Codex where the workflows differ.
