# Replay studies

> Living reference — current findings and priorities: [BACKLOG.md](../../BACKLOG.md).

## DUE timing audit

Create an isolated Python 3.12 environment (`venv312`) and install the repository's
requirements. The existing `venv` can stay intact. Run from the repository root:

```sh
./venv312/bin/python tools/studies/due_timing_study.py --all --output data/due_timing_audit_NEW.json
OVERCUT_PYTHON=./venv312/bin/python ./check.sh --full
./venv312/bin/python src/validate_races.py --wec
```

The study also supports explicit input paths, or `--wec`. Outputs are paired JSON
and Markdown; existing evidence is never overwritten. Missing or failed inputs,
invalid configuration, configuration drift, source changes during the run, incomplete races,
and absent measurable evidence fail the command.

Samples observe calculator decisions after prediction logging. Replay observers
are optional and read-only: `on_sample(conn, oid, ts_ms, ctx, cars)` on both batch
paths, and `on_event(client, channel, data, ts_ms)` on WEC capture replay. Observer
exceptions fail the study; normal callers retain their existing behavior.

The target cadence is five recorded seconds. Coarser source frames are not
interpolated: actual sample gaps, cycle counts, and observed time span are recorded.
Each visit is matched to the episode active at its last sampled pre-entry
observation, bounded by the preceding pit exit. Withdrawn episodes and episodes
with no later observed entry are separate. Unknown-lap/unobserved intervals and
visits after calculator finish are excluded, with reasons retained.

The legacy WEC comparator uses the previous stint gate with identical VET inputs
and threshold. IMSA only uses its current policy. Scoring sees future pit truth
after replay finishes; the calculator never receives that truth. Results measure
observed pit visits, not fuel necessity or renderer freshness. Timeliness among
all eligible visits includes missed warnings; conditional lead means only cover
warned visits. Live acceptance remains in the backlog.

Compare current and legacy rows within WEC: pooled current summaries also include
IMSA, for which this study does not evaluate a legacy policy.

## Fresh WEC energy authority comparison

```sh
./venv312/bin/python tools/studies/wec_vet_due_study.py --all --output data/wec_vet_due_NEW.json
```

Three policies share one chronological replay: current fresh-energy authority,
the shipped additive rule (unaged raw VET), and additive VET with freshness only.
The calculator retains the unmodified stint decision for passive comparison;
NET/finish/stop inputs are unchanged. Fresh WEC energy uses the existing 5% gate;
above it, energy overrides the stint DUE call. Missing/stale energy retains the
stint fallback. Recorded energy timestamps establish replay freshness against
recorded analysis time. The standard legacy-stint comparator now shares the same
fresh-energy authority as the current policy; older dated evidence is preserved.

Per-stop records support paired gain/loss comparisons. Distinguish independent
events from alternate feeds for the same event; no-VET inputs verify fallback,
not cross-event generalisation of energy authority. Current findings: BACKLOG.md.
