import type { Battle, CarRow as CarRowData, ClassGroup } from '../types';
import CarRow from './CarRow';

const CLASS_SPINE: Record<string, string> = {
  'GTP':      '#4FC3F7',
  'HYPERCAR': '#4FC3F7',
  'LMP2':     '#81C784',
  'GTD PRO':  '#FF8A65',
  'LMGT3':    '#FFD54F',
  'GTD':      '#CE93D8',
};

interface Props {
  group: ClassGroup;
  selectedCar: string | null;
  battles: Battle[];
  onSelectCar: (car: CarRowData, classCode: string) => void;
}

export default function ClassSection({ group, selectedCar, battles, onSelectCar }: Props) {
  const spineColor = CLASS_SPINE[group.code] ?? '#6b7280';

  return (
    <div className="rounded-md overflow-hidden border border-border/60">
      {/* Class header */}
      <div
        className="flex items-center gap-2 px-3 py-1.5 text-[10px] font-heading font-bold tracking-widest uppercase"
        style={{ background: spineColor + '18', borderBottom: `1px solid ${spineColor}30` }}
      >
        <span className="w-1.5 h-3.5 rounded-sm" style={{ background: spineColor }} />
        <span style={{ color: spineColor }}>{group.code}</span>
        <span className="text-muted-fg font-body font-normal ml-1">
          {group.rows.length} cars
        </span>
      </div>

      {/* Column header */}
      <div className="flex items-center h-6 px-3 text-[9px] uppercase tracking-wider text-muted-fg border-b border-border/40 bg-card/40">
        <div className="w-7 shrink-0 text-center">P</div>
        <div className="w-[3px] shrink-0 mx-2" />
        <div className="w-9 shrink-0 text-center">Car</div>
        <div className="w-[168px] shrink-0 pr-2">Driver / Team</div>
        <div className="w-[176px] shrink-0">Stint · Fuel</div>
        <div className="w-[76px] shrink-0 text-right pr-2">Next Stop</div>
        <div className="w-[104px] shrink-0 pl-2">Last Lap</div>
        <div className="w-[88px] shrink-0 text-right pr-2">Gap</div>
        <div className="w-9 shrink-0 text-center">Stops</div>
        <div className="w-[60px] shrink-0 text-center">Status</div>
        <div className="w-10 shrink-0 text-center text-primary/70">NET</div>
        <div className="flex-1 min-w-0 pl-3">Notes</div>
      </div>

      {/* Rows — battles scoped to this class so a car-number collision in
          another class can never attach the wrong note */}
      {group.rows.map((row, i) => (
        <CarRow
          key={row.car}
          row={row}
          index={i}
          spineColor={spineColor}
          selected={selectedCar === row.car}
          battles={battles.filter((b) => b.carClass === group.code)}
          onClick={() => onSelectCar(row, group.code)}
        />
      ))}
    </div>
  );
}
