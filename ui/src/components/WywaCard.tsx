import { useState } from 'react';
import type { RcMessage, RowsPayload } from '../types';

// Minimum away time before showing the card (2 minutes)
const MIN_AWAY_S = 120;

export interface WywaSummary {
  awaySecs: number;
  alertRc: RcMessage[];          // tier-2 RC events while away
  contextRcCount: number;        // tier-1 RC events while away
  classChanges: { code: string; prev: string; curr: string }[]; // class leader changes
}

/** Compute what happened between snapshot and current payload while the user was away. */
export function buildWywaSummary(
  snapshot: RowsPayload,
  current: RowsPayload,
  awayFrom: number,
): WywaSummary | null {
  const awaySecs = (Date.now() - awayFrom) / 1000;
  if (awaySecs < MIN_AWAY_S) return null;

  // RC events that arrived while away
  const alertRc: RcMessage[] = [];
  let contextRcCount = 0;
  for (const m of current.rcMessages) {
    if (m.ts !== null && m.ts > awayFrom) {
      if (m.tier === 2) alertRc.push(m);
      else if (m.tier === 1) contextRcCount++;
    }
  }

  // Class leader changes (net P1)
  const prevLeaders = new Map<string, string>();
  for (const cls of snapshot.classes) {
    const p1 = cls.rows.find((r) => r.netPos === 1) ?? cls.rows[0];
    if (p1) prevLeaders.set(cls.code, p1.car);
  }
  const classChanges: WywaSummary['classChanges'] = [];
  for (const cls of current.classes) {
    const p1 = cls.rows.find((r) => r.netPos === 1) ?? cls.rows[0];
    if (!p1) continue;
    const prev = prevLeaders.get(cls.code);
    if (prev && prev !== p1.car) {
      classChanges.push({ code: cls.code, prev, curr: p1.car });
    }
  }

  return { awaySecs, alertRc, contextRcCount, classChanges };
}

function fmtAway(s: number): string {
  if (s < 3600) return `${Math.round(s / 60)}m`;
  const h = Math.floor(s / 3600);
  const m = Math.round((s % 3600) / 60);
  return `${h}h ${m}m`;
}

interface Props {
  summary: WywaSummary;
  onDismiss: () => void;
}

export default function WywaCard({ summary, onDismiss }: Props) {
  const [expanded, setExpanded] = useState(false);

  const totalEvents = summary.alertRc.length + summary.contextRcCount + summary.classChanges.length;
  const hasDetail = totalEvents > 0;

  return (
    <div className="mx-3 mt-2 mb-1 rounded-md border border-amber-500/30 bg-amber-500/5 overflow-hidden">
      {/* Header row */}
      <div className="flex items-center gap-2 px-3 py-2">
        <span className="text-[9px] font-heading font-bold tracking-widest text-amber-400 uppercase shrink-0">
          WYWA
        </span>
        <span className="text-[11px] font-body text-fg/80">
          Away {fmtAway(summary.awaySecs)}
        </span>
        {totalEvents > 0 && (
          <span className="text-[10px] text-muted-fg">
            — {totalEvents} event{totalEvents !== 1 ? 's' : ''}
          </span>
        )}
        {totalEvents === 0 && (
          <span className="text-[10px] text-muted-fg">— no notable changes</span>
        )}

        <div className="flex items-center gap-2 ml-auto">
          {hasDetail && (
            <button
              onClick={() => setExpanded((e) => !e)}
              className="text-[9px] font-heading font-bold text-amber-400/70 hover:text-amber-400 uppercase tracking-wider"
            >
              {expanded ? 'LESS ▲' : 'MORE ▼'}
            </button>
          )}
          <button
            onClick={onDismiss}
            className="text-muted-fg hover:text-fg text-base leading-none"
            aria-label="Dismiss"
          >
            ×
          </button>
        </div>
      </div>

      {/* Inline budget chips (always visible) */}
      {totalEvents > 0 && !expanded && (
        <div className="flex flex-wrap gap-1.5 px-3 pb-2">
          {summary.classChanges.map((c) => (
            <span key={c.code} className="text-[9px] font-body px-1.5 py-0.5 rounded bg-emerald-900/40 text-emerald-300">
              {c.code} lead: #{c.prev}→#{c.curr}
            </span>
          ))}
          {summary.alertRc.length > 0 && (
            <span className="text-[9px] font-body px-1.5 py-0.5 rounded bg-amber-900/40 text-amber-300">
              {summary.alertRc.length} RC alert{summary.alertRc.length !== 1 ? 's' : ''}
            </span>
          )}
          {summary.contextRcCount > 0 && (
            <span className="text-[9px] font-body px-1.5 py-0.5 rounded bg-white/10 text-muted-fg">
              +{summary.contextRcCount} context
            </span>
          )}
        </div>
      )}

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t border-amber-500/20 px-3 py-2 space-y-1">
          {summary.classChanges.map((c) => (
            <div key={c.code} className="text-[10px] font-body text-fg/80">
              <span className="text-emerald-400 font-bold">{c.code}</span>
              {' '}lead change: #{c.prev} → #{c.curr}
            </div>
          ))}
          {summary.alertRc.map((m, i) => (
            <div key={i} className="text-[10px] font-body text-amber-300/90">
              ● {m.message}
            </div>
          ))}
          {summary.contextRcCount > 0 && (
            <div className="text-[10px] text-muted-fg/60">
              +{summary.contextRcCount} procedural RC message{summary.contextRcCount !== 1 ? 's' : ''} (context)
            </div>
          )}
        </div>
      )}
    </div>
  );
}
