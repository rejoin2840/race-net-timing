import type { CarRow as CarRowType } from '../types';

const IN_PIT  = new Set(['BOX', 'PIT', 'STOPPED']);
const OUT_LAP = new Set(['OUT_LAP', 'OUT']);

function fmtGap(gapMs: number | null, pos: number): string {
  if (pos === 1 || gapMs === null || gapMs === 0) return '—';
  if (gapMs >= 600_000) return '+?L';
  const s = gapMs / 1000;
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
        {row.team && (
          <div className="text-[10px] text-muted-fg font-body truncate leading-none mt-0.5">
            {row.team}
          </div>
        )}
      </div>

      {/* Gap */}
      <div className="w-20 shrink-0 text-right pr-3 font-body tabular-nums text-[11px] text-muted-fg">
        {fmtGap(row.gapMs, row.pos)}
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
