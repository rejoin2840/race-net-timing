import type { Battle, CarRow, ClassGroup, RcMessage } from '../types';

// ── colour maps ────────────────────────────────────────────────────────────
const SPINE: Record<string, string> = {
  'GTP': '#4FC3F7', 'HYPERCAR': '#4FC3F7', 'LMP2': '#81C784',
  'GTD PRO': '#FF8A65', 'LMGT3': '#FFD54F', 'GTD': '#CE93D8',
};
const KIND_COLOR: Record<string, string> = {
  dq:              '#ef4444',
  penalty:         '#f59e0b',
  rescinded:       '#22c55e',
  retired:         '#6b7280',
  flag:            '#f59e0b',
  review:          '#f59e0b',
  warning:         '#f59e0b',
  incident:        '#d1d5db',
  unparsed_penalty:'#f59e0b',
};

function spineColor(cls: string) { return SPINE[cls] ?? '#6b7280'; }

// ── small helpers ─────────────────────────────────────────────────────────
function RailLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[9px] font-heading font-bold tracking-widest text-muted-fg/70 uppercase mb-1 px-0.5">
      {children}
    </div>
  );
}

function Divider() {
  return <div className="border-t border-border/40 my-2.5" />;
}

function fmtRcAge(ts: number | null): string {
  if (ts === null || ts <= 0) return '';  // feed stores 0 when it has no timestamp
  const s = Math.round((Date.now() - ts) / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m`;
}

function fmtGapS(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`;
}

// ── BATTLES section ───────────────────────────────────────────────────────
function Battles({ battles }: { battles: Battle[] }) {
  return (
    <section>
      <RailLabel>Battles</RailLabel>
      {battles.length === 0 ? (
        <span className="text-[10px] text-muted-fg/50 font-body">none close</span>
      ) : (
        <div className="flex flex-col gap-0.5">
          {battles.map((b, i) => (
            <div key={i} className="flex items-center gap-1 text-[10px] font-body leading-tight">
              <span className="shrink-0 font-bold" style={{ color: spineColor(b.carClass) }}>
                {b.carClass}
              </span>
              {b.closing ? (
                <>
                  <span className="text-fg/80">#{b.carChaser}</span>
                  <span className="text-amber-400 font-bold">↑</span>
                  <span className="text-fg/80">#{b.carAhead}</span>
                  <span className="text-fg/60 tabular-nums">{fmtGapS(b.gapMs)}</span>
                  {b.rateSPerLap !== null && (
                    <span className="text-muted-fg/60 tabular-nums">−{b.rateSPerLap.toFixed(1)}/L</span>
                  )}
                </>
              ) : (
                <>
                  <span className="text-fg/60">#{b.carAhead}</span>
                  <span className="text-muted-fg/40">▸</span>
                  <span className="text-fg/60">#{b.carChaser}</span>
                  <span className="text-muted-fg/50 tabular-nums">{fmtGapS(b.gapMs)}</span>
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

// ── PROJECTED PODIUM section ──────────────────────────────────────────────
function ProjectedPodium({ classes }: { classes: ClassGroup[] }) {
  // collect per-class projected-finish ranking
  const byClass = classes.map(({ code, rows }) => {
    const ranked = rows
      .filter((r) => r.projectedFinish !== null)
      .sort((a, b) => (a.projectedFinish ?? 99) - (b.projectedFinish ?? 99))
      .slice(0, 3);
    return { code, ranked };
  }).filter(({ ranked }) => ranked.length > 0);

  if (byClass.length === 0) return (
    <section>
      <RailLabel>Projected Podium</RailLabel>
      <span className="text-[10px] text-muted-fg/50 font-body">—</span>
    </section>
  );

  return (
    <section>
      <RailLabel>Projected Podium</RailLabel>
      <div className="flex flex-col gap-1.5">
        {byClass.map(({ code, ranked }) => (
          <div key={code}>
            <div className="text-[9px] font-bold mb-0.5" style={{ color: spineColor(code) }}>
              {code}
            </div>
            <div className="flex gap-1.5">
              {ranked.map((r, i) => (
                <div key={r.car} className="flex items-baseline gap-0.5 text-[10px] font-body">
                  <span className="text-muted-fg/50 text-[9px]">{i + 1}.</span>
                  <span className={i === 0 ? 'text-fg font-bold' : 'text-fg/70'}>#{r.car}</span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

// ── RACE CONTROL section ──────────────────────────────────────────────────
function RaceControl({ rcMessages }: { rcMessages: RcMessage[] }) {
  const visible = rcMessages.filter((m) => m.tier === null || m.tier > 0).slice(0, 6);
  return (
    <section>
      <RailLabel>Race Control</RailLabel>
      {visible.length === 0 ? (
        <span className="text-[10px] text-muted-fg/50 font-body">no messages</span>
      ) : (
        <div className="flex flex-col gap-1">
          {visible.map((m, i) => {
            const color = m.kind ? (KIND_COLOR[m.kind] ?? '#d1d5db') : '#6b7280';
            const dim = m.tier === 1;
            return (
              <div key={i} className="flex gap-1 items-start text-[10px] font-body leading-tight">
                <span className="shrink-0 mt-px" style={{ color, opacity: dim ? 0.6 : 1 }}>●</span>
                <span className="flex-1 min-w-0 break-words" style={{ color: dim ? '#9ca3af' : '#d1d5db' }}>
                  {m.message}
                </span>
                <span className="shrink-0 text-[9px] text-muted-fg/40 tabular-nums">
                  {fmtRcAge(m.ts)}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

// ── DUE TO PIT section ────────────────────────────────────────────────────
function DueToPit({ classes }: { classes: ClassGroup[] }) {
  const due: { cls: string; car: string }[] = [];
  for (const { code, rows } of classes) {
    for (const r of rows) {
      if (r.fuelDue === 'due') due.push({ cls: code, car: r.car });
    }
  }
  return (
    <section>
      <RailLabel>Due to Pit</RailLabel>
      {due.length === 0 ? (
        <span className="text-[10px] text-muted-fg/50 font-body">none</span>
      ) : (
        <div className="flex flex-wrap gap-x-2 gap-y-0.5">
          {due.map(({ cls, car }) => (
            <div key={car} className="flex items-center gap-1 text-[10px] font-body">
              <span className="font-bold" style={{ color: spineColor(cls) }}>{cls}</span>
              <span className="text-amber-400 font-bold">#{car}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

// ── RACE AT A GLANCE section ──────────────────────────────────────────────
function RaceAtAGlance({ classes }: { classes: ClassGroup[] }) {
  const leaders = classes
    .map(({ code, rows }) => {
      const leader = rows.find((r) => r.netPos === 1) ?? rows[0] ?? null;
      return leader ? { code, car: leader.car, laps: leader.laps } : null;
    })
    .filter(Boolean) as { code: string; car: string; laps: number }[];

  return (
    <section>
      <RailLabel>Race at a Glance</RailLabel>
      {leaders.length === 0 ? (
        <span className="text-[10px] text-muted-fg/50 font-body">—</span>
      ) : (
        <div className="flex flex-col gap-0.5">
          {leaders.map(({ code, car, laps }) => (
            <div key={code} className="flex items-center gap-1 text-[10px] font-body">
              <span className="w-14 shrink-0 font-bold truncate" style={{ color: spineColor(code) }}>
                {code}
              </span>
              <span className="text-fg/80 font-bold">#{car}</span>
              <span className="text-muted-fg/50 tabular-nums ml-auto">{laps}L</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

// ── Root export ───────────────────────────────────────────────────────────
interface RightRailProps {
  classes: ClassGroup[];
  rcMessages: RcMessage[];
  battles: Battle[];
}

export default function RightRail({ classes, rcMessages, battles }: RightRailProps) {
  return (
    <aside className="w-[210px] shrink-0 border-l border-border overflow-y-auto thin-scrollbar bg-bg px-3 py-3 flex flex-col gap-0">
      <RaceAtAGlance classes={classes} />
      <Divider />
      <Battles battles={battles} />
      <Divider />
      <DueToPit classes={classes} />
      <Divider />
      <ProjectedPodium classes={classes} />
      <Divider />
      <RaceControl rcMessages={rcMessages} />
    </aside>
  );
}
