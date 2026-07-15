import { useEffect, useState } from 'react';
import type { CarRow, RowsPayload } from './types';
import { MOCK_PAYLOAD } from './mock';
import Board from './components/Board';
import DetailPanel from './components/DetailPanel';

const FLAG_LABEL: Record<string, string> = {
  GF: 'GREEN', YF: 'YELLOW', FCY: 'FULL-COURSE YELLOW',
  SC: 'SAFETY CAR', VSC: 'VIRTUAL SC', RF: 'RED FLAG', CH: 'CHECKERED',
};
const FLAG_COLOR: Record<string, string> = {
  GF: '#166534', YF: '#713f12', FCY: '#713f12',
  SC: '#713f12', VSC: '#713f12', RF: '#7f1d1d', CH: '#374151',
};
const CLASS_SPINE: Record<string, string> = {
  'GTP': '#4FC3F7', 'HYPERCAR': '#4FC3F7', 'LMP2': '#81C784',
  'GTD PRO': '#FF8A65', 'LMGT3': '#FFD54F', 'GTD': '#CE93D8',
};

function fmtAge(ageS: number | null): string {
  if (ageS === null || ageS < 0) return '';
  if (ageS < 5) return 'LIVE';
  return `${Math.round(ageS)}s ago`;
}

export default function App() {
  const [payload, setPayload]       = useState<RowsPayload | null>(null);
  const [selectedCar, setSelected]  = useState<{ car: CarRow; classCode: string } | null>(null);

  useEffect(() => {
    if (window.racenet) {
      const cb = (p: RowsPayload) => setPayload(p);
      window.racenet.onRows(cb);
      return () => window.racenet!.offRows(cb);
    } else {
      setPayload(MOCK_PAYLOAD);
      const t = setInterval(() => setPayload({ ...MOCK_PAYLOAD, updatedAt: Date.now() }), 2000);
      return () => clearInterval(t);
    }
  }, []);

  // Keep selected car data fresh on every payload update
  useEffect(() => {
    if (!selectedCar || !payload) return;
    for (const cls of payload.classes) {
      const fresh = cls.rows.find((r) => r.car === selectedCar.car.car);
      if (fresh) { setSelected({ car: fresh, classCode: cls.code }); return; }
    }
  }, [payload]);

  const { session } = payload ?? { session: { flag: null, lap: null, isRunning: false, ageS: null } };
  const flagColor = session.flag ? (FLAG_COLOR[session.flag] ?? '#374151') : '#374151';
  const flagLabel = session.flag ? (FLAG_LABEL[session.flag] ?? session.flag) : '—';
  const ageLabel  = fmtAge(session.ageS);

  function handleSelect(car: CarRow, classCode: string) {
    setSelected((prev) => (prev?.car.car === car.car ? null : { car, classCode }));
  }

  return (
    <div className="h-full flex flex-col bg-bg overflow-hidden">
      {/* Header */}
      <header
        className="flex items-center gap-4 px-4 py-2 border-b border-border shrink-0 transition-colors duration-500"
        style={{ backgroundColor: flagColor + '33' }}
      >
        <span
          className="text-[10px] font-heading font-bold tracking-widest px-2 py-0.5 rounded"
          style={{ background: flagColor, color: '#fff' }}
        >
          {flagLabel}
        </span>
        {session.lap !== null && (
          <span className="font-heading font-bold text-sm text-fg/70">LAP {session.lap}</span>
        )}
        <span className="ml-auto text-[10px] font-body text-muted-fg tabular-nums">{ageLabel}</span>
        {!payload && (
          <span className="text-[10px] text-muted-fg">waiting for data…</span>
        )}
        {!window.racenet && (
          <span className="text-[10px] text-amber-500/70 font-body">MOCK DATA</span>
        )}
      </header>

      {/* Body: board + optional detail panel */}
      <div className="flex-1 min-h-0 flex overflow-hidden">
        <div className="flex-1 min-w-0 overflow-y-auto thin-scrollbar">
          {payload ? (
            <Board
              classes={payload.classes}
              selectedCar={selectedCar?.car.car ?? null}
              onSelectCar={handleSelect}
            />
          ) : (
            <div className="flex items-center justify-center h-full text-muted-fg text-sm font-body">
              Waiting for race data…
            </div>
          )}
        </div>

        {selectedCar && (
          <DetailPanel
            car={selectedCar.car}
            classCode={selectedCar.classCode}
            spineColor={CLASS_SPINE[selectedCar.classCode] ?? '#6b7280'}
            onClose={() => setSelected(null)}
          />
        )}
      </div>
    </div>
  );
}
