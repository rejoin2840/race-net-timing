import type { RowsPayload } from './types';

export const STALE_MS = 12_000;
export function ageMs(stamp: string | null | undefined, now: number): number | null {
  if (!stamp) return null;
  const iso = /(Z|[+-]\d\d:?\d\d)$/.test(stamp) ? stamp : stamp + 'Z';
  const age = now - Date.parse(iso);
  return Number.isFinite(age) && age >= -2000 ? Math.max(0, age) : null;
}

export function agePayload(p: RowsPayload, now: number): RowsPayload {
  const transportAge = Math.max(0, now - p.updatedAt);
  const sourceAge = p.session.ageS === null ? null : p.session.ageS * 1000 + transportAge;
  const computationStale = p.classes.some(cls => cls.rows.some(row => {
    const age = ageMs(row.netUpdatedAt, now);
    return age === null || age > STALE_MS;
  }));
  const stale = computationStale || transportAge > STALE_MS || sourceAge === null || sourceAge > STALE_MS;
  const finished = p.session.isFinished;
  const isRace = p.session.sessionType === undefined || p.session.sessionType === 'RACE';
  return {
    ...p,
    session: { ...p.session, ageS: sourceAge === null ? null : sourceAge / 1000,
      health: finished ? 'finished' : stale ? 'stale' : 'live' },
    battles: stale || !isRace ? [] : p.battles,
    classes: p.classes.map(cls => ({ ...cls, rows: cls.rows.map(row => {
      const timingAge = ageMs(row.sourceUpdatedAt ?? (p.session.mode === 'mock' ? row.netUpdatedAt : null), now);
      const computedAge = ageMs(row.netUpdatedAt, now);
      const untrusted = !isRace || (!finished && (stale || timingAge === null || timingAge > STALE_MS
        || computedAge === null || computedAge > STALE_MS));
      if (!untrusted) return row;
      return { ...row, netPos: null, netGapMs: null, netGapBandMs: null,
        projectedFinish: null, fuelPct: null, fuelDue: null, catching: null, catchInLaps: null,
        strategyNote: null, fuelLapsLeft: null, mustPitLap: null,
        nextStopMs: null, nextStopStdMs: null, stopsLeft: null };
    }) })),
  };
}

export function onTrackLeader(rows: { car: string; pos: number }[]): string | undefined {
  return [...rows].filter(r => r.pos > 0).sort((a, b) => a.pos - b.pos)[0]?.car;
}
