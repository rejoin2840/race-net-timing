import type { Battle, CarRow as CarRowType } from '../types';

const IN_PIT  = new Set(['BOX', 'PIT', 'STOPPED']);
const OUT_LAP = new Set(['OUT_LAP', 'OUT']);

function fmtGap(row: CarRowType): string {
  if (row.pos === 1) return '—';
  if (row.lapsDown && row.lapsDown > 0) return `+${row.lapsDown}L`;
  // class gap (from net_analysis) is gap to CLASS leader — the number that
  // belongs in a per-class table. Raw feed gapMs (gap to OVERALL leader,
  // 0 = lapped sentinel) is only a fallback before the Poller has run.
  const g = row.classGapMs ?? row.gapMs;
  if (g === null || g === 0) return '—';
  const s = g / 1000;
  if (s < 60) return `+${s.toFixed(3)}`;
  const m = Math.floor(s / 60);
  const rem = (s % 60).toFixed(3).padStart(6, '0');
  return `+${m}:${rem}`;
}

function fmtLapTime(ms: number | null): string {
  if (ms === null) return '—';
  const s = ms / 1000;
  const m = Math.floor(s / 60);
  const rem = (s % 60).toFixed(3).padStart(6, '0');
  return `${m}:${rem}`;
}


// personal-best delta for the LAST LAP column — 'best' when this lap tied or
// beat the car's own best (equality still reads 'best', not '+0.00')
function lapDelta(lastMs: number | null, bestMs: number | null): { text: string; isBest: boolean } {
  if (lastMs === null || bestMs === null) return { text: '', isBest: false };
  const d = lastMs - bestMs;
  if (d <= 0) return { text: 'best', isBest: true };
  return { text: `+${(d / 1000).toFixed(2)}`, isBest: false };
}

type NoteTone = 'penalty' | 'battle' | 'strategy' | 'quiet';

// priority order: a penalty always wins the notes lane (it's the one thing
// that must never be missed); otherwise combine a live battle call with the
// engine's undercut/overcut read, since both can be true at once
function buildNote(row: CarRowType, battles: Battle[]): { text: string; tone: NoteTone } {
  if (row.penaltyNote) return { text: row.penaltyNote, tone: 'penalty' };

  const chasing = battles.find((b) => b.carChaser === row.car && b.closing);
  const parts: string[] = [];
  if (chasing) {
    // rate > 5 s/lap is a pit-cycle artifact, nulled engine-side (poller) —
    // this guard is defensive for older DB rows written before that fix
    const rate = (chasing.rateSPerLap !== null && chasing.rateSPerLap <= 5)
      ? ` (gaining ${chasing.rateSPerLap.toFixed(1)}s/lap)` : '';
    parts.push(`▲ closing on #${chasing.carAhead} — ${(chasing.gapMs / 1000).toFixed(1)}s${rate}`);
  }
  if (row.strategyNote) parts.push(row.strategyNote);

  if (parts.length === 0) return { text: '—', tone: 'quiet' };
  return { text: parts.join(' · '), tone: chasing ? 'battle' : 'strategy' };
}

const NOTE_COLOR: Record<NoteTone, string> = {
  penalty:  'text-amber-400',
  battle:   'text-emerald-400/90',
  strategy: 'text-amber-400/60',
  quiet:    'text-muted-fg/30',
};

interface Props {
  row: CarRowType;
  index: number;
  spineColor: string;
  selected: boolean;
  battles: Battle[];
  onOpen: () => void;
}

// Single click opens (reverted from double-click after the 07-16 feel-test:
// with click-anywhere-to-close, an accidental open costs one click to undo,
// so the deliberate-open protection wasn't worth the friction). Propagation
// is stopped so the open doesn't immediately bubble to App's catch-all close.
export default function CarRow({ row, index, spineColor, selected, battles, onOpen }: Props) {
  const inPit  = IN_PIT.has(row.trackStatus ?? '');
  const outLap = OUT_LAP.has(row.trackStatus ?? '');
  const dim    = inPit || !row.isRunning;
  const delta  = lapDelta(row.lastLapMs, row.bestLapMs);
  const note   = buildNote(row, battles);

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={(e) => { e.stopPropagation(); onOpen(); }}
      onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && onOpen()}
      className={`flex items-center h-11 border-b border-border/40 cursor-pointer transition-colors
        ${selected
          ? 'bg-primary/10 border-l-2'
          : index % 2 === 1
          ? 'bg-white/[0.012]'
          : ''}
        ${!selected && !dim ? 'hover:bg-white/[0.04]' : ''}
        ${dim && !selected ? 'opacity-50' : ''}
      `}
      style={selected ? { borderLeftColor: spineColor } : undefined}
    >
      {/* Position */}
      <div className="w-7 shrink-0 text-center font-heading font-bold text-base tabular-nums">
        {row.pos}
      </div>

      {/* Class spine */}
      <div
        className="w-[3px] h-[22px] rounded-sm shrink-0 mx-2"
        style={{ background: spineColor }}
      />

      {/* Car number */}
      <div className="w-9 shrink-0 text-center">
        <span className="text-[10px] font-heading font-bold tracking-wide text-muted-fg">
          #{row.car}
        </span>
      </div>

      {/* Driver / Team */}
      <div className="w-[168px] shrink-0 pr-2 overflow-hidden">
        <div className="font-heading font-bold text-[13px] tracking-wide truncate leading-none">
          {row.driver || row.car}
        </div>
        <div className="text-[10px] text-muted-fg font-body truncate mt-0.5">
          {row.team}
        </div>
      </div>

      {/* Last lap — leads the metric cluster now (owner's scan: pace first) */}
      <div className="w-[104px] shrink-0 font-body tabular-nums text-[11px] text-muted-fg">
        {row.lastLapMs !== null ? (
          <>
            <span className="text-fg/90">{fmtLapTime(row.lastLapMs)}</span>
            {delta.text && (
              <span className={`ml-1 text-[9px] ${delta.isBest ? 'text-emerald-400' : 'text-muted-fg/50'}`}>
                {delta.text}
              </span>
            )}
          </>
        ) : '—'}
      </div>

      {/* Stint · fuel — stays adjacent to next stop; one story ("how empty,
          and what the stop costs") split across two columns */}
      <div className="w-[176px] shrink-0 flex items-center gap-2 pl-2">
        {row.fuelPct !== null ? (
          <>
            <div className="w-10 h-[5px] rounded-sm bg-white/10 overflow-hidden shrink-0">
              <div
                className="h-full rounded-sm"
                style={{
                  width: `${Math.max(0, Math.min(100, row.fuelPct))}%`,
                  background: row.fuelDue === 'due' ? '#d19a3d' : '#5d6b7d',
                }}
              />
            </div>
            <span className="text-[11px] tabular-nums text-muted-fg w-8 text-right">
              {row.fuelPct.toFixed(0)}%
            </span>
          </>
        ) : (
          /* width = bar(40) + gap(8) + pct(32) so stint laps stay column-
             aligned between cars with and without fuel telemetry */
          <span className="text-[10px] text-muted-fg/25 w-[80px]">—</span>
        )}
        <span className="text-[11px] tabular-nums text-muted-fg">
          {row.stintLaps !== null ? `${row.stintLaps}L` : ''}
        </span>
        {row.fuelDue === 'due' && (
          <span className="text-[9px] font-heading font-bold text-amber-400 tracking-wide ml-auto">
            DUE
          </span>
        )}
      </div>

      {/* Next stop — laps left in the tank. The "by Lxxx" session lap was
          dropped 07-17 (owner: noise); mustPitLap still arrives in the
          payload if it's ever wanted back. */}
      <div className="w-[72px] shrink-0 text-center font-body tabular-nums text-[11px]">
        {row.fuelLapsLeft !== null ? (
          <span className="text-fg/90 font-semibold">~{row.fuelLapsLeft}L</span>
        ) : <span className="text-muted-fg">—</span>}
      </div>

      {/* Gap */}
      <div className="w-[96px] shrink-0 text-center font-body tabular-nums text-[11px] text-muted-fg">
        {fmtGap(row)}
      </div>

      {/* Stops */}
      <div className="w-9 shrink-0 text-center font-body tabular-nums text-[11px] text-muted-fg">
        {row.stops > 0 ? row.stops : <span className="text-muted-fg/30">—</span>}
      </div>

      {/* Pit status */}
      <div className="w-[60px] shrink-0 flex justify-center">
        {inPit ? (
          <span className="text-[9px] font-heading font-bold px-1.5 py-0.5 rounded bg-blue-900/60 text-blue-300 tracking-wider">
            IN PIT
          </span>
        ) : outLap ? (
          <span className="text-[9px] font-heading font-bold px-1.5 py-0.5 rounded bg-emerald-900/60 text-emerald-300 tracking-wider">
            OUT
          </span>
        ) : null}
      </div>

      {/* Net position */}
      <NetCell pos={row.pos} netPos={row.netPos} settled={row.netSettled} netUpdatedAt={row.netUpdatedAt} />

      {/* Notes — far right, flex, quiet dash when nothing to say */}
      <div className={`flex-1 min-w-0 pl-3 pr-3 text-[11px] font-body truncate ${NOTE_COLOR[note.tone]}`}>
        {note.text}
      </div>
    </div>
  );
}

const STALE_AFTER_MS = 12_000;

function isStale(netUpdatedAt: string | null): boolean {
  if (!netUpdatedAt) return false;
  // the poller stamps '+00:00' (not 'Z') — only append 'Z' when the string
  // carries no timezone at all; appending to an existing offset parses as NaN
  const iso = /(Z|[+-]\d\d:?\d\d)$/.test(netUpdatedAt) ? netUpdatedAt : netUpdatedAt + 'Z';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return false;
  return Date.now() - t > STALE_AFTER_MS;
}

function NetCell({ pos, netPos, settled, netUpdatedAt }: {
  pos: number; netPos: number | null; settled: boolean; netUpdatedAt: string | null;
}) {
  if (netPos === null) {
    return <div className="w-10 shrink-0 text-center text-muted-fg/20 text-[10px]">—</div>;
  }
  const stale = isStale(netUpdatedAt);
  const delta = pos - netPos; // positive = gaining (net ahead of track)
  const colorClass = stale
    ? 'text-muted-fg/25'
    : settled
    ? 'text-muted-fg/40'
    : delta > 0
    ? 'text-emerald-400'
    : delta < 0
    ? 'text-rose-400'
    : 'text-muted-fg/60';

  return (
    <div className={`w-10 shrink-0 text-center tabular-nums font-heading font-bold text-[11px] ${colorClass}`}>
      {delta > 0 ? '▲' : delta < 0 ? '▼' : ''}{netPos}
    </div>
  );
}
