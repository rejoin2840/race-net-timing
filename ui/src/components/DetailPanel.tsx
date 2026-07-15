import type { CarRow } from '../types';

interface Props {
  car: CarRow;
  classCode: string;
  spineColor: string;
  onClose: () => void;
}

function fmtMs(ms: number | null): string {
  if (ms === null || ms === undefined) return '—';
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const rem = (s % 60).toFixed(1).padStart(4, '0');
  return `${m}m ${rem}s`;
}

function fmtGapMs(ms: number | null, pos: number): string {
  if (pos === 1 || ms === null || ms === 0) return '—';
  const s = ms / 1000;
  if (s < 60) return `+${s.toFixed(3)}s`;
  const m = Math.floor(s / 60);
  const rem = (s % 60).toFixed(3).padStart(6, '0');
  return `+${m}:${rem}`;
}

const FLAG_LABELS: Record<string, string> = {
  GF: 'Green', YF: 'Yellow', SC: 'Safety Car', FCY: 'FCY', RF: 'Red',
};

export default function DetailPanel({ car, classCode, spineColor, onClose }: Props) {
  const hasNet = car.netPos !== null;
  const netDelta = car.pos - (car.netPos ?? car.pos);

  return (
    <div className="w-[320px] shrink-0 flex flex-col border-l border-border/60 bg-card overflow-y-auto">
      {/* Header */}
      <div
        className="flex items-start justify-between px-4 py-3 border-b border-border/60"
        style={{ borderLeft: `3px solid ${spineColor}` }}
      >
        <div>
          <div className="flex items-center gap-2">
            <span className="font-heading font-bold text-xl" style={{ color: spineColor }}>
              #{car.car}
            </span>
            <span className="text-[10px] font-heading font-bold tracking-widest uppercase text-muted-fg">
              {classCode}
            </span>
          </div>
          <div className="font-heading font-bold text-sm mt-0.5">{car.driver || car.car}</div>
          {car.team && (
            <div className="text-[10px] text-muted-fg mt-0.5">{car.team}</div>
          )}
        </div>
        <button
          onClick={onClose}
          className="text-muted-fg hover:text-fg text-lg leading-none mt-0.5"
          aria-label="Close"
        >
          ×
        </button>
      </div>

      {/* Net position headline */}
      <div className="px-4 py-3 border-b border-border/40">
        <div className="text-[9px] uppercase tracking-wider text-muted-fg mb-1">Net position</div>
        <div className="flex items-baseline gap-3">
          <span className="font-heading font-bold text-3xl">
            {hasNet ? `P${car.netPos}` : '—'}
          </span>
          {hasNet && netDelta !== 0 && (
            <span className={`text-sm font-heading font-bold ${netDelta > 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
              {netDelta > 0 ? `▲${netDelta}` : `▼${Math.abs(netDelta)}`} vs track
            </span>
          )}
          {hasNet && car.netSettled && (
            <span className="text-[9px] text-muted-fg/60 font-heading uppercase tracking-wider">settled</span>
          )}
        </div>
      </div>

      {/* Net math breakdown */}
      <div className="px-4 py-3 border-b border-border/40 space-y-2">
        <div className="text-[9px] uppercase tracking-wider text-muted-fg mb-2">Gap breakdown</div>

        <Row
          label="On-track gap"
          value={
            car.lapsDown && car.lapsDown > 0
              ? `+${car.lapsDown} lap${car.lapsDown > 1 ? 's' : ''}`
              : fmtGapMs(car.classGapMs ?? car.gapMs, car.pos)
          }
        />

        {hasNet && (
          <>
            <Row label="Stops left" value={car.stopsLeft !== null ? String(car.stopsLeft) : '—'} />

            {(car.penaltyS ?? 0) > 0 && (
              <Row
                label={car.penaltyNote || 'Penalty carry'}
                value={`+${car.penaltyS!.toFixed(0)}s`}
                highlight="rose"
              />
            )}

            {car.owesDC && (
              <Row label="Driver change owed" value="+" highlight="amber" />
            )}

            <div className="border-t border-border/40 pt-2 mt-1">
              <div className="flex justify-between items-baseline">
                <span className="text-[10px] font-heading font-bold uppercase tracking-wide text-fg">
                  Net gap
                </span>
                <span className="font-heading font-bold text-sm tabular-nums">
                  {car.netPos === 1
                    ? '—'
                    : car.netGapMs !== null
                    ? `${fmtGapMs(car.netGapMs, car.netPos ?? car.pos)}${
                        car.netGapBandMs ? ` ±${(car.netGapBandMs / 1000).toFixed(0)}s` : ''
                      }`
                    : '—'}
                </span>
              </div>
            </div>
          </>
        )}

        {!hasNet && (
          <div className="text-[10px] text-muted-fg/50 italic">
            Net math available once Poller has run
          </div>
        )}
      </div>

      {/* Pit history */}
      <div className="px-4 py-3 flex-1">
        <div className="text-[9px] uppercase tracking-wider text-muted-fg mb-2">
          Pit history · {car.stops} stop{car.stops !== 1 ? 's' : ''}
        </div>
        {car.pitEvents.length === 0 ? (
          <div className="text-[10px] text-muted-fg/50 italic">No stops recorded</div>
        ) : (
          <table className="w-full text-[10px]">
            <thead>
              <tr className="text-[9px] text-muted-fg/70 uppercase tracking-wider">
                <th className="text-left pb-1 font-normal w-6">#</th>
                <th className="text-left pb-1 font-normal">Lap</th>
                <th className="text-left pb-1 font-normal">Flag</th>
                <th className="text-right pb-1 font-normal">Duration</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/20">
              {car.pitEvents.map((p) => (
                <tr key={p.stop} className="h-7">
                  <td className="font-heading font-bold text-muted-fg pr-2">{p.stop}</td>
                  <td className="tabular-nums">{p.lap ?? '—'}</td>
                  <td>
                    {p.flag ? (
                      <span className={`text-[9px] px-1 py-0.5 rounded font-heading font-bold
                        ${p.flag === 'GF' ? 'bg-emerald-900/40 text-emerald-400' :
                          p.flag === 'YF' || p.flag === 'FCY' || p.flag === 'SC' ? 'bg-yellow-900/40 text-yellow-400' :
                          'bg-white/10 text-muted-fg'}`}>
                        {FLAG_LABELS[p.flag] ?? p.flag}
                      </span>
                    ) : '—'}
                  </td>
                  <td className="text-right tabular-nums">{fmtMs(p.durationMs)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function Row({ label, value, highlight }: { label: string; value: string; highlight?: 'rose' | 'amber' }) {
  const valueClass = highlight === 'rose' ? 'text-rose-400' : highlight === 'amber' ? 'text-amber-400' : 'text-muted-fg';
  return (
    <div className="flex justify-between items-baseline">
      <span className="text-[10px] text-muted-fg">{label}</span>
      <span className={`text-[11px] font-body tabular-nums ${valueClass}`}>{value}</span>
    </div>
  );
}
