import type { RowsPayload } from './types';

// mirror the poller's timestamp format ('+00:00' offset, not 'Z') so the mock
// exercises the exact parse path live data takes
const pollerIso = (agoMs = 0) =>
  new Date(Date.now() - agoMs).toISOString().replace('Z', '+00:00');

const noNet = {
  netPos: null, netGapMs: null, netGapBandMs: null,
  classGapMs: null, lapsDown: null,
  stopsLeft: null, penaltyS: null, penaltyNote: null,
  owesDC: false, netSettled: false,
  projectedFinish: null, fuelDue: null, catching: null,
  catchInLaps: null, strategyNote: null,
  fuelLapsLeft: null, mustPitLap: null,
  nextStopMs: null, nextStopStdMs: null, classLeaderStopsLeft: null,
  netUpdatedAt: null,
  pitEvents: [],
};

// stint/fuel/pace fields are independent of net analysis (they come straight
// off standings_current) — separate default so a car can have lap-time data
// without net math, or vice versa
const noLapData = { lastLapMs: null, bestLapMs: null, fuelPct: null, stintLaps: null };

// A function, not a constant: timestamps must be minted per tick or the whole
// mock board ages past the 12s stale guard and every NET cell greys out.
export const buildMockPayload = (): RowsPayload => ({
  session: { flag: 'GF', lap: 23, isRunning: true, ageS: 1.2,
             finalType: 'BY_TIME', remainingS: 9252, finalLaps: null, isFinished: false },
  rcMessages: [
    { ts: Date.now() - 90000,  message: '#60 - Drive Through Penalty - Pit Lane Speeding', tier: 2, kind: 'penalty', detectedAt: pollerIso(90000) },
    { ts: Date.now() - 420000, message: 'Full Course Yellow - Incident at Turn 5',          tier: 2, kind: 'flag',    detectedAt: pollerIso(420000) },
    { ts: Date.now() - 900000, message: 'Green Flag - Racing Resumed',                       tier: 1, kind: 'flag',    detectedAt: pollerIso(900000) },
  ],
  battles: [
    { carClass: 'GTP', carAhead: '10', carChaser: '31', gapMs: 1240, closing: true,  rateSPerLap: 0.4 },
    { carClass: 'GTD', carAhead: '57', carChaser: '44', gapMs: 1890, closing: false, rateSPerLap: null },
  ],
  classes: [
    {
      code: 'GTP',
      rows: [
        { car: '10', pos: 1, driver: 'ALBUQUERQUE, Filipe', team: 'Wayne Taylor Racing',     gapMs: 0,      laps: 47, trackStatus: 'TRACK',   stops: 2, isRunning: true,
          lastLapMs: 76842, bestLapMs: 76512, fuelPct: 61, stintLaps: 11,
          netPos: 1, netGapMs: 0, netGapBandMs: null, classGapMs: 0, lapsDown: 0, stopsLeft: 0, penaltyS: null, penaltyNote: null, owesDC: false, netSettled: true,
          projectedFinish: 1, fuelDue: null, catching: null, catchInLaps: null, strategyNote: null,
          fuelLapsLeft: 17, mustPitLap: 64,
          nextStopMs: 48500, nextStopStdMs: 1200, classLeaderStopsLeft: 0,
          netUpdatedAt: pollerIso(),
          pitEvents: [{ stop: 1, lap: 12, flag: 'GF', durationMs: 48200 }, { stop: 2, lap: 28, flag: 'GF', durationMs: 49100 }] },
        { car: '31', pos: 2, driver: 'DERANI, Pipo',      team: 'Whelen Engineering',      gapMs: 12345,  laps: 47, trackStatus: 'TRACK',   stops: 2, isRunning: true,
          lastLapMs: 76512, bestLapMs: 76512, fuelPct: 58, stintLaps: 12,
          netPos: 3, netGapMs: 52000, netGapBandMs: 9000, classGapMs: 12345, lapsDown: 0, stopsLeft: 1, penaltyS: null, penaltyNote: null, owesDC: true, netSettled: false,
          projectedFinish: 3, fuelDue: null, catching: null, catchInLaps: null, strategyNote: '#10 must stop first — overcut chance',
          fuelLapsLeft: 16, mustPitLap: 63,
          nextStopMs: 47200, nextStopStdMs: 900, classLeaderStopsLeft: 0,
          netUpdatedAt: pollerIso(),
          pitEvents: [{ stop: 1, lap: 11, flag: 'GF', durationMs: 47900 }, { stop: 2, lap: 26, flag: 'YF', durationMs: 38500 }] },
        { car: '7',  pos: 3, driver: 'CONWAY, Mike',      team: 'Acura ARX-06',            gapMs: 34012,  laps: 47, trackStatus: 'BOX',     stops: 1, isRunning: false,
          lastLapMs: null, bestLapMs: 77104, fuelPct: 96, stintLaps: 0,
          netPos: 2, netGapMs: 14200, netGapBandMs: 8000, classGapMs: 34012, lapsDown: 0, stopsLeft: 0, penaltyS: null, penaltyNote: null, owesDC: false, netSettled: false,
          projectedFinish: 2, fuelDue: 'due', catching: '10', catchInLaps: 4.2, strategyNote: null,
          fuelLapsLeft: 28, mustPitLap: 75,
          nextStopMs: 49100, nextStopStdMs: 1400, classLeaderStopsLeft: 0,
          netUpdatedAt: pollerIso(),
          pitEvents: [{ stop: 1, lap: 14, flag: 'GF', durationMs: 49400 }] },
        { car: '60', pos: 4, driver: 'JARVIS, Oliver',      team: 'Meyer Shank Racing',      gapMs: 56789,  laps: 46, trackStatus: 'TRACK',   stops: 2, isRunning: true,
          lastLapMs: 78001, bestLapMs: 76523, fuelPct: 8, stintLaps: 18,
          netPos: 4, netGapMs: 58000, netGapBandMs: 5000, classGapMs: null, lapsDown: 1, stopsLeft: 1, penaltyS: 30, penaltyNote: 'Drive-through penalty', owesDC: false, netSettled: false,
          projectedFinish: 4, fuelDue: 'due', catching: null, catchInLaps: null, strategyNote: null,
          fuelLapsLeft: 2, mustPitLap: 48,
          nextStopMs: 48800, nextStopStdMs: 1100, classLeaderStopsLeft: 0,
          netUpdatedAt: pollerIso(60_000),  // stale on purpose — demos the grey-NET guard
          pitEvents: [{ stop: 1, lap: 10, flag: 'GF', durationMs: 48700 }, { stop: 2, lap: 25, flag: 'GF', durationMs: 48900 }] },
        { car: '93', pos: 5, driver: 'HEISTAND, Ryan',    team: 'Racers Edge Motorsports', gapMs: 78234,  laps: 46, trackStatus: 'OUT_LAP', stops: 2, isRunning: true,
          lastLapMs: null, bestLapMs: 77890, fuelPct: 49, stintLaps: 13,
          ...noNet, pitEvents: [{ stop: 1, lap: 13, flag: 'GF', durationMs: 49200 }, { stop: 2, lap: 29, flag: 'GF', durationMs: 48600 }] },
        // pos 6-7: past the TOP_N=5 fold — exercises the class accordion in browser mode
        { car: '35', pos: 6, driver: 'WILKINS, Mark',     team: 'ARC Bratislava',          gapMs: 89012,  laps: 46, trackStatus: 'TRACK',   stops: 2, isRunning: true,
          ...noNet, ...noLapData, pitEvents: [] },
        { car: '81', pos: 7, driver: 'DELETRAZ, Louis',    team: 'DragonSpeed',             gapMs: 95678,  laps: 45, trackStatus: 'TRACK',   stops: 3, isRunning: true,
          ...noNet, ...noLapData, pitEvents: [] },
      ],
    },
    {
      code: 'GTD PRO',
      rows: [
        { car: '14', pos: 1, driver: 'LEGGE, Katherine',    team: 'VasserSullivan',    gapMs: 0,     laps: 43, trackStatus: 'TRACK', stops: 3, isRunning: true,
          lastLapMs: 81420, bestLapMs: 80990, fuelPct: null, stintLaps: 9,
          netPos: 1, netGapMs: 0, netGapBandMs: null, classGapMs: 0, lapsDown: 0, stopsLeft: 0, penaltyS: null, penaltyNote: null, owesDC: false, netSettled: true,
          projectedFinish: 1, fuelDue: null, catching: null, catchInLaps: null, strategyNote: null,
          fuelLapsLeft: 23, mustPitLap: 66,
          nextStopMs: 62000, nextStopStdMs: 1800, classLeaderStopsLeft: 0,
          netUpdatedAt: pollerIso(),
          pitEvents: [{ stop: 1, lap: 9, flag: 'GF', durationMs: 62100 }, { stop: 2, lap: 20, flag: 'YF', durationMs: 52000 }, { stop: 3, lap: 33, flag: 'GF', durationMs: 61800 }] },
        { car: '23', pos: 2, driver: 'VAN DER LINDE, Sheldon', team: 'Heart of Racing',   gapMs: 8901,  laps: 43, trackStatus: 'TRACK', stops: 3, isRunning: true,
          ...noNet, ...noLapData, pitEvents: [{ stop: 1, lap: 8, flag: 'GF', durationMs: 63200 }, { stop: 2, lap: 21, flag: 'GF', durationMs: 62500 }, { stop: 3, lap: 32, flag: 'GF', durationMs: 62100 }] },
        { car: '79', pos: 3, driver: 'MACNEIL, Cooper',  team: 'WeatherTech Racing', gapMs: 21345, laps: 43, trackStatus: 'TRACK', stops: 3, isRunning: true,
          ...noNet, ...noLapData, pitEvents: [] },
      ],
    },
    {
      code: 'GTD',
      rows: [
        { car: '57', pos: 1, driver: 'SELLERS, Bryan', team: 'Winward Racing', gapMs: 0,     laps: 42, trackStatus: 'TRACK', stops: 3, isRunning: true, ...noNet, ...noLapData, pitEvents: [] },
        { car: '44', pos: 2, driver: 'POTTER, John',  team: 'Magnus Racing',  gapMs: 15678, laps: 42, trackStatus: 'TRACK', stops: 3, isRunning: true, ...noNet, ...noLapData, pitEvents: [] },
      ],
    },
  ],
  updatedAt: Date.now(),
});
