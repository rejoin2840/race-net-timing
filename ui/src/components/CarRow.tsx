import type { CarRow as CarRowType } from '../types';

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

interface Props {
  row: CarRowType;
  index: number;
  spineColor: string;
  selected: boolean;
  onClick: () => void;
}

export default function CarRow({ row, index, spineColor, selected, onClick }: Props) {
  const inPit  = IN_PIT.has(row.trackStatus ?? '');
  const outLap = OUT_LAP.has(row.trackStatus ?? '');
  const dim    = inPit || !row.isRunning;

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && onClick()}
      className={`flex items-center h-9 border-b border-border/40 cursor-pointer transition-colors
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
      <div className="w-6 shrink-0 text-center font-heading font-bold text-sm tabular-nums">
        {row.pos}
      </div>

      {/* Class spine */}
      <div
        className="w-[3px] h-[22px] rounded-sm shrink-0 mx-2"
        style={{ background: spineColor }}
      />

      {/* Car number */}
      <div className="w-8 shrink-0 text-center">
        <span className="text-[10px] font-heading font-bold tracking-wide text-muted-fg">
          #{row.car}
        </span>
      </div>

      {/* Driver / Team */}
      <div className="flex-1 min-w-0 pr-2 overflow-hidden">
        <div className="font-heading font-bold text-[13px] tracking-wide truncate leading-none">
          {row.driver || row.car}
        </div>
        <div className="flex items-center gap-1.5 leading-none mt-0.5">
          {row.team && (
            <span className="text-[10px] text-muted-fg font-body truncate">
              {row.team}
            </span>
          )}
          {row.strategyNote && (
            <span className="text-[9px] text-amber-400/70 font-body truncate shrink-0 max-w-[160px]">
              {row.strategyNote}
            </span>
          )}
          {!row.strategyNote && row.fuelDue === 'due' && (
            <span className="text-[9px] text-orange-400/80 font-body shrink-0">
              pit due
            </span>
          )}
        </div>
      </div>

      {/* Gap */}
      <div className="w-20 shrink-0 text-right pr-3 font-body tabular-nums text-[11px] text-muted-fg">
        {fmtGap(row)}
      </div>

      {/* Stops */}
      <div className="w-10 shrink-0 text-center font-body tabular-nums text-[11px] text-muted-fg">
        {row.stops > 0 ? row.stops : <span className="text-muted-fg/30">—</span>}
      </div>

      {/* Pit status */}
      <div className="w-14 shrink-0 flex justify-center">
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
      <NetCell pos={row.pos} netPos={row.netPos} settled={row.netSettled} />
    </div>
  );
}

function NetCell({ pos, netPos, settled }: { pos: number; netPos: number | null; settled: boolean }) {
  if (netPos === null) {
    return <div className="w-10 shrink-0 text-center text-muted-fg/20 text-[10px]">—</div>;
  }
  const delta = pos - netPos; // positive = gaining (net ahead of track)
  const colorClass = settled
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
