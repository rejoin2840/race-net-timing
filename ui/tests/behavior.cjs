'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const root = path.resolve(__dirname, '..');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'overcut-ui-tests-'));
try {
  execFileSync(path.join(root, 'node_modules/.bin/tsc'), [
    'src/health.ts', 'src/mock.ts', 'src/components/WywaCard.tsx',
    '--module', 'commonjs', '--target', 'ES2020', '--jsx', 'react-jsx',
    '--esModuleInterop', '--skipLibCheck', '--strict', '--outDir', tmp,
  ], { cwd: root, stdio: 'inherit' });
  fs.symlinkSync(path.join(root, 'node_modules'), path.join(tmp, 'node_modules'), 'dir');
  const { agePayload, onTrackLeader } = require(path.join(tmp, 'health.js'));
  const { buildMockPayload } = require(path.join(tmp, 'mock.js'));
  const { buildWywaSummary } = require(path.join(tmp, 'components/WywaCard.js'));
  const payload = buildMockPayload();
  assert.equal(agePayload(payload, payload.updatedAt).session.health, 'live');
  const stalled = agePayload(payload, payload.updatedAt + 13000);
  assert.equal(stalled.session.health, 'stale');
  assert.equal(stalled.battles.length, 0);
  assert(stalled.classes.every(c => c.rows.every(r => r.netPos === null && r.fuelDue === null)));
  const unknown = structuredClone(payload);
  unknown.session.ageS = null;
  assert.equal(agePayload(unknown, payload.updatedAt).session.health, 'stale');
  const deadCompute = structuredClone(payload);
  deadCompute.classes[0].rows[0].netUpdatedAt = new Date(Date.now() - 20000).toISOString();
  assert.equal(agePayload(deadCompute, Date.now()).session.health, 'stale');
  assert.equal(agePayload(deadCompute, Date.now()).classes[0].rows[0].netPos, null);
  const before = structuredClone(payload);
  const after = structuredClone(payload);
  after.classes[0].rows[0].netPos = 2;
  after.classes[0].rows[1].netPos = 1;
  assert.equal(buildWywaSummary(before, after, Date.now() - 180000).events.filter(e => e.tone === 'lead').length, 0);
  after.classes[0].rows[0].pos = 2;
  after.classes[0].rows[1].pos = 1;
  assert.equal(buildWywaSummary(before, after, Date.now() - 180000).events[0].curr, after.classes[0].rows[1].car);
  after.session.oid = 'new-race';
  assert.equal(buildWywaSummary(before, after, Date.now() - 180000), null);
  assert.equal(onTrackLeader([{car: '7', pos: 2}, {car: '9', pos: 1}]), '9');
  // Schema queries pin all components to an explicit session.
  const schema = require('../electron/schema.cjs');
  const probe = {naCols: new Set(), rcHasTier: false, hasFuelPct: false,
    hasTimingUpdated: false, hasTelemetryUpdated: false, hasReplayClock: false};
  const queries = [schema.buildMainSql(probe), schema.buildPitsSql(),
    schema.buildRcSql(probe), schema.buildBattlesSql(), schema.buildSessionCompSql()];
  execFileSync('python3', ['-c', `
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).parent / 'src'))
from db import RaceDB
from poller import Poller
from types import SimpleNamespace
store=RaceDB(':memory:')
for oid, car in [('first','7'),('second','9')]:
    store.set_session(oid, {'name':'Race','type':'RACE'})
    store.upsert_entry(car, {'class':'GTP'})
    store.ingest_car(car, {'laps':10,'pos_in_class':1}, {'class':'GTP','isRunning':True},10)
store.commit()
poller=Poller()
poller._ensure_rail_battles_table(store.conn)
poller._write_session_computed(store.conn,'first',SimpleNamespace(remaining_s=3600,elapsed_s=100))
queries=json.load(sys.stdin)
for sql in queries:
    rows=store.conn.execute(sql, ('first',)).fetchall()
    if 'FROM standings_current' in sql:
        assert len(rows)==1 and rows[0]['car_number']=='7', 'mixed sessions'
store.close()
` , root], { input: JSON.stringify(queries), stdio: ['pipe', 'inherit', 'inherit'] });
  console.log('UI behavior checks passed: freshness, silence, computation loss, WYWA, session scope');
} finally { fs.rmSync(tmp, { recursive: true, force: true }); }
