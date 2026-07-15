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
}

export interface ClassGroup {
  code: string;
  rows: CarRow[];
}

export interface RowsPayload {
  session: {
    flag: string | null;
    lap: number | null;
    isRunning: boolean;
    ageS: number | null;
  };
  classes: ClassGroup[];
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
