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
  stopsLeft: number | null;
  penaltyS: number | null;
  penaltyNote: string | null;
  owesDC: boolean;
  netSettled: boolean;
  pitEvents: PitEvent[];
}

export interface ClassGroup {
  code: string;
  rows: CarRow[];
}

export interface RcMessage {
  ts: number | null;
  message: string;
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
