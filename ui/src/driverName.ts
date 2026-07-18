// The timing feed delivers drivers as "LASTNAME, First" (e.g. "BAMBER, Earl",
// "VAN DER LINDE, Sheldon"). On the board that reads as shouting and puts the
// less-scannable half first, so display formats to "First Lastname".
//
// Only ALL-CAPS words are re-cased — anything already mixed-case is presumed
// correct and passed through. No comma means the name is not in feed format
// (mock data, or a car-number fallback); it is returned untouched.

// surname particles conventionally lowercased (van der Linde, de Vries).
// "Di"/"Del" style Italian particles are conventionally capitalized, so they
// are NOT listed and fall through to normal capitalization.
const PARTICLES = new Set(['van', 'der', 'den', 'ter', 'de', 'von', 'da', 'dos', 'do', 'la', 'le', 'e']);
const KEEP_UPPER = new Set(['II', 'III', 'IV']); // generation suffixes

function recaseWord(w: string): string {
  if (!/^[A-Z'’.-]+$/.test(w)) return w;               // already mixed-case
  if (KEEP_UPPER.has(w)) return w;
  if (w === 'JR' || w === 'SR') return w[0] + 'r';
  // hyphenated compounds: recase each side (SMITH-JONES -> Smith-Jones)
  if (w.includes('-')) return w.split('-').map(recaseWord).join('-');
  const lower = w.toLowerCase();
  if (PARTICLES.has(lower)) return lower;
  if (/^O['’]/.test(w)) return w.slice(0, 2) + cap(w.slice(2));   // O'WARD -> O'Ward
  if (w.startsWith('MC')) return 'Mc' + cap(w.slice(2));          // MCLAUGHLIN -> McLaughlin
  return cap(w);
}

function cap(s: string): string {
  return s.charAt(0) + s.slice(1).toLowerCase();
}

export function formatDriverName(raw: string): string {
  const comma = raw.indexOf(',');
  if (comma < 0) return raw;
  const last = raw.slice(0, comma).trim().split(/\s+/).map(recaseWord).join(' ');
  const first = raw.slice(comma + 1).trim();
  return first ? `${first} ${last}` : last;
}
