import type { RowsPayload } from './types';

export const MOCK_PAYLOAD: RowsPayload = {
  session: { flag: 'GF', lap: 23, isRunning: true, ageS: 1.2 },
  classes: [
    {
      code: 'GTP',
      rows: [
        { car: '10', pos: 1, driver: 'F. Albuquerque', team: 'Wayne Taylor Racing',    gapMs: 0,      laps: 47, trackStatus: 'TRACK', stops: 2, isRunning: true },
        { car: '31', pos: 2, driver: 'P. Derani',      team: 'Whelen Engineering',     gapMs: 12345,  laps: 47, trackStatus: 'TRACK', stops: 2, isRunning: true },
        { car: '7',  pos: 3, driver: 'M. Conway',      team: 'Acura ARX-06',           gapMs: 34012,  laps: 47, trackStatus: 'BOX',   stops: 1, isRunning: false },
        { car: '60', pos: 4, driver: 'O. Jarvis',      team: 'Meyer Shank Racing',     gapMs: 56789,  laps: 46, trackStatus: 'TRACK', stops: 2, isRunning: true },
        { car: '93', pos: 5, driver: 'R. Heistand',    team: 'Racers Edge Motorsports', gapMs: 78234, laps: 46, trackStatus: 'OUT_LAP', stops: 2, isRunning: true },
      ],
    },
    {
      code: 'GTD PRO',
      rows: [
        { car: '14', pos: 1, driver: 'K. Legge',       team: 'VasserSullivan',          gapMs: 0,     laps: 43, trackStatus: 'TRACK', stops: 3, isRunning: true },
        { car: '23', pos: 2, driver: 'R. Bernhard',    team: 'Heart of Racing',          gapMs: 8901,  laps: 43, trackStatus: 'TRACK', stops: 3, isRunning: true },
        { car: '79', pos: 3, driver: 'C. MacNeil',     team: 'WeatherTech Racing',       gapMs: 21345, laps: 43, trackStatus: 'TRACK', stops: 3, isRunning: true },
      ],
    },
    {
      code: 'GTD',
      rows: [
        { car: '57', pos: 1, driver: 'B. Sellers',     team: 'Winward Racing',           gapMs: 0,     laps: 42, trackStatus: 'TRACK', stops: 3, isRunning: true },
        { car: '44', pos: 2, driver: 'J. Potter',      team: 'Magnus Racing',            gapMs: 15678, laps: 42, trackStatus: 'TRACK', stops: 3, isRunning: true },
      ],
    },
  ],
  updatedAt: Date.now(),
};
