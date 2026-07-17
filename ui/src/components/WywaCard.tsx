import { useState } from 'react';
import type { RcMessage, RowsPayload } from '../types';

// Minimum away time before showing the card (2 minutes)
const MIN_AWAY_S = 120;
// Collapsed default shows this many ranked events — NOT zero. A collapse
// that hides every event summarizes nothing; "MORE" extends past this count,
// it doesn't reveal the first event.
const COLLAPSED_N = 3;

export type WywaEvent =
  | { key: string; tone: 'lead'; classCode: string; prev: string; curr: string }
  | { key: string; tone: 'alert'; text: string };

export interface WywaSummary {
  awaySecs: number;
  events: WywaEvent[];       // ranked, most important first: lead changes, then RC alerts
  contextRcCount: number;    // tier-1 RC events while away (footer count, not ranked)
}

/** Compute what happened between snapshot and current payload while the user was away. */
export function buildWywaSummary(
  snapshot: RowsPayload,
  current: RowsPayload,
  awayFrom: number,
): WywaSummary | null {
  const awaySecs = (Date.now() - awayFrom) / 1000;
  if (awaySecs < MIN_AWAY_S) return null;

  // RC alerts that arrived while away (current.rcMessages is already ts DESC,
  // so this preserves most-recent-first without a re-sort). "Arrived" is
  // judged on detectedAt (wall-clock at ingest) when present — the raw `ts`
  // is the message's race-original time, which during a replay is weeks in
  // the past and would never test > awayFrom, silently emptying WYWA of RC
  // events in every feel-test.
  const alertEvents: WywaEvent[] = [];
  let contextRcCount = 0;
  for (const m of current.rcMessages) {
    let arrived: number | null = m.ts;
    if (m.detectedAt) {
      const iso = /(Z|[+-]\d\d:?\d\d)$/.test(m.detectedAt) ? m.detectedAt : m.detectedAt + 'Z';
      const t = new Date(iso).getTime();
      if (!Number.isNaN(t)) arrived = t;
    }
    if (arrived !== null && arrived > awayFrom) {
      if (m.tier === 2) {
        alertEvents.push({ key: `rc-${m.ts}-${m.message}`, tone: 'alert', text: m.message });
      } else if (m.tier === 1) {
        contextRcCount++;
      }
    }
  }

  // Class leader changes (net P1) — ranked above RC alerts: the identity of
  // the race changing is the single most important catch-up fact
  const prevLeaders = new Map<string, string>();
  for (const cls of snapshot.classes) {
    const p1 = cls.rows.find((r) => r.netPos === 1) ?? cls.rows[0];
    if (p1) prevLeaders.set(cls.code, p1.car);
  }
  const leadEvents: WywaEvent[] = [];
  for (const cls of current.classes) {
    const p1 = cls.rows.find((r) => r.netPos === 1) ?? cls.rows[0];
    if (!p1) continue;
    const prev = prevLeaders.get(cls.code);
    if (prev && prev !== p1.car) {
      leadEvents.push({ key: `lead-${cls.code}`, tone: 'lead', classCode: cls.code, prev, curr: p1.car });
    }
  }

  return { awaySecs, events: [...leadEvents, ...alertEvents], contextRcCount };
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

  const total = summary.events.length;
  const hasMore = total > COLLAPSED_N;
  const visible = expanded ? summary.events : summary.events.slice(0, COLLAPSED_N);
  const showBody = visible.length > 0 || summary.contextRcCount > 0;

  return (
    <div
      className="mx-3 mt-2 mb-1 rounded-md border border-amber-500/30 bg-amber-500/5 overflow-hidden"
      onClick={(e) => e.stopPropagation()}  // sits inside App's click-anywhere-closes-panel region; must not double as a click-through
    >
      {/* Header row */}
      <div className="flex items-center gap-2 px-3 py-2">
        <span className="text-[9px] font-heading font-bold tracking-widest text-amber-400 uppercase shrink-0">
          WYWA
        </span>
        <span className="text-[11px] font-body text-fg/80">
          Away {fmtAway(summary.awaySecs)}
        </span>
        <span className="text-[10px] text-muted-fg">
          {total > 0 ? `— ${total} event${total !== 1 ? 's' : ''}` : '— no notable changes'}
        </span>

        <div className="flex items-center gap-2 ml-auto">
          {hasMore && (
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

      {/* Ranked events — collapsed shows top 3, MORE extends to the full list */}
      {showBody && (
        <div className="border-t border-amber-500/20 px-3 py-2 space-y-1">
          {visible.map((ev) => ev.tone === 'lead' ? (
            <div key={ev.key} className="text-[11px] leading-normal font-body text-fg/80">
              <span className="text-emerald-400 font-bold">{ev.classCode}</span>
              {' '}lead change: #{ev.prev} → #{ev.curr}
            </div>
          ) : (
            <div key={ev.key} className="text-[11px] leading-normal font-body text-amber-300/90">
              ● {ev.text}
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
