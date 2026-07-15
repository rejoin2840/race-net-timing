import type { ClassGroup } from '../types';
import ClassSection from './ClassSection';

const CLASS_ORDER = ['GTP', 'HYPERCAR', 'LMP2', 'GTD PRO', 'LMGT3', 'GTD'];

interface Props {
  classes: ClassGroup[];
}

export default function Board({ classes }: Props) {
  const sorted = [...classes].sort((a, b) => {
    const ai = CLASS_ORDER.indexOf(a.code);
    const bi = CLASS_ORDER.indexOf(b.code);
    if (ai === -1 && bi === -1) return a.code.localeCompare(b.code);
    if (ai === -1) return 1;
    if (bi === -1) return -1;
    return ai - bi;
  });

  return (
    <div className="py-2 px-3 flex flex-col gap-4">
      {sorted.map((cls) => (
        <ClassSection key={cls.code} group={cls} />
      ))}
    </div>
  );
}
