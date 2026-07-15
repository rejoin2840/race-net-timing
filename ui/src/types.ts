export interface PitEvent {
  stop: number;
  lap: number | null;
  flag: string | null;
  durationMs: number | null;
}

export interface CarRow {
  car: string;
  pos: number;
  driver: string;
  team: string;
  gapMs: number | null;
  laps: number;
  trackStatus: string | null;
  stops: number;
  isRunning: boolean;
  // net analysis (null when Poller hasn't run yet)
  netPos: number | null;
  netGapMs: number | null;
  netGapBandMs: number | null;
  classGapMs: number | null; // gap to CLASS leader — preferred over raw gapMs
  lapsDown: number | null;
  stopsLeft: number | null;
  penaltyS: number | null;
  penaltyNote: string | null;
  owesDC: boolean;
  netSettled: boolean;
  projectedFinish: number | null;
  fuelDue: string | null;        // 'due' or null
  catching: string | null;       // car number being caught
  catchInLaps: number | null;
  strategyNote: string | null;
  netUpdatedAt: string | null;   // ISO timestamp — for stale-data guard
  pitEvents: PitEvent[];
}

export interface ClassGroup {
  code: string;
  rows: CarRow[];
}

export interface RcMessage {
  ts: number | null;
  message: string;
  tier: number | null;   // 0=suppress, 1=context, 2=alert
  kind: string | null;   // 'penalty' | 'dq' | 'retired' | 'flag' | 'rescinded' | 'review' | 'incident' | 'warning' | ''
}

export interface Battle {
  carClass: string;
  carAhead: string;
  carChaser: string;
  gapMs: number;
  closing: boolean;
  rateSPerLap: number | null;
}

export interface RowsPayload {
  session: {
    flag: string | null;
    lap: number | null;
    isRunning: boolean;
    ageS: number | null;
    finalType: 'BY_TIME' | 'BY_LAPS' | null;
    remainingS: number | null;
    finalLaps: number | null;
    isFinished: boolean;
  };
  classes: ClassGroup[];
  rcMessages: RcMessage[];
  battles: Battle[];
  updatedAt: number;
}

declare global {
  interface Window {
    racenet?: {
      onRows: (cb: (payload: RowsPayload) => void) => void;
      offRows: (cb: (payload: RowsPayload) => void) => void;
    };
  }
}
