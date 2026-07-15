import type { RowsPayload } from './types';

const noNet = {
  netPos: null, netGapMs: null, netGapBandMs: null,
  stopsLeft: null, penaltyS: null, penaltyNote: null,
  owesDC: false, netSettled: false, pitEvents: [],
};

export const MOCK_PAYLOAD: RowsPayload = {
  session: { flag: 'GF', lap: 23, isRunning: true, ageS: 1.2 },
  classes: [
    {
      code: 'GTP',
      rows: [
        { car: '10', pos: 1, driver: 'F. Albuquerque', team: 'Wayne Taylor Racing',     gapMs: 0,      laps: 47, trackStatus: 'TRACK',   stops: 2, isRunning: true,
          netPos: 1, netGapMs: 0, netGapBandMs: null, stopsLeft: 0, penaltyS: null, penaltyNote: null, owesDC: false, netSettled: true,
          pitEvents: [{ stop: 1, lap: 12, flag: 'GF', durationMs: 48200 }, { stop: 2, lap: 28, flag: 'GF', durationMs: 49100 }] },
        { car: '31', pos: 2, driver: 'P. Derani',      team: 'Whelen Engineering',      gapMs: 12345,  laps: 47, trackStatus: 'TRACK',   stops: 2, isRunning: true,
          netPos: 2, netGapMs: 14500, netGapBandMs: 6000, stopsLeft: 1, penaltyS: null, penaltyNote: null, owesDC: true, netSettled: false,
          pitEvents: [{ stop: 1, lap: 11, flag: 'GF', durationMs: 47900 }, { stop: 2, lap: 26, flag: 'YF', durationMs: 38500 }] },
        { car: '7',  pos: 3, driver: 'M. Conway',      team: 'Acura ARX-06',            gapMs: 34012,  laps: 47, trackStatus: 'BOX',     stops: 1, isRunning: false,
          netPos: 3, netGapMs: 28000, netGapBandMs: 8000, stopsLeft: 1, penaltyS: null, penaltyNote: null, owesDC: false, netSettled: false,
          pitEvents: [{ stop: 1, lap: 14, flag: 'GF', durationMs: 49400 }] },
        { car: '60', pos: 4, driver: 'O. Jarvis',      team: 'Meyer Shank Racing',      gapMs: 56789,  laps: 46, trackStatus: 'TRACK',   stops: 2, isRunning: true,
          netPos: 4, netGapMs: 58000, netGapBandMs: 5000, stopsLeft: 1, penaltyS: 30, penaltyNote: 'Drive-through penalty', owesDC: false, netSettled: false,
          pitEvents: [{ stop: 1, lap: 10, flag: 'GF', durationMs: 48700 }, { stop: 2, lap: 25, flag: 'GF', durationMs: 48900 }] },
        { car: '93', pos: 5, driver: 'R. Heistand',    team: 'Racers Edge Motorsports', gapMs: 78234,  laps: 46, trackStatus: 'OUT_LAP', stops: 2, isRunning: true,
          ...noNet, pitEvents: [{ stop: 1, lap: 13, flag: 'GF', durationMs: 49200 }, { stop: 2, lap: 29, flag: 'GF', durationMs: 48600 }] },
      ],
    },
    {
      code: 'GTD PRO',
      rows: [
        { car: '14', pos: 1, driver: 'K. Legge',    team: 'VasserSullivan',    gapMs: 0,     laps: 43, trackStatus: 'TRACK', stops: 3, isRunning: true,
          netPos: 1, netGapMs: 0, netGapBandMs: null, stopsLeft: 0, penaltyS: null, penaltyNote: null, owesDC: false, netSettled: true,
          pitEvents: [{ stop: 1, lap: 9, flag: 'GF', durationMs: 62100 }, { stop: 2, lap: 20, flag: 'YF', durationMs: 52000 }, { stop: 3, lap: 33, flag: 'GF', durationMs: 61800 }] },
        { car: '23', pos: 2, driver: 'R. Bernhard', team: 'Heart of Racing',   gapMs: 8901,  laps: 43, trackStatus: 'TRACK', stops: 3, isRunning: true,
          ...noNet, pitEvents: [{ stop: 1, lap: 8, flag: 'GF', durationMs: 63200 }, { stop: 2, lap: 21, flag: 'GF', durationMs: 62500 }, { stop: 3, lap: 32, flag: 'GF', durationMs: 62100 }] },
        { car: '79', pos: 3, driver: 'C. MacNeil',  team: 'WeatherTech Racing', gapMs: 21345, laps: 43, trackStatus: 'TRACK', stops: 3, isRunning: true,
          ...noNet, pitEvents: [] },
      ],
    },
    {
      code: 'GTD',
      rows: [
        { car: '57', pos: 1, driver: 'B. Sellers', team: 'Winward Racing', gapMs: 0,     laps: 42, trackStatus: 'TRACK', stops: 3, isRunning: true, ...noNet, pitEvents: [] },
        { car: '44', pos: 2, driver: 'J. Potter',  team: 'Magnus Racing',  gapMs: 15678, laps: 42, trackStatus: 'TRACK', stops: 3, isRunning: true, ...noNet, pitEvents: [] },
      ],
    },
  ],
  updatedAt: Date.now(),
};
