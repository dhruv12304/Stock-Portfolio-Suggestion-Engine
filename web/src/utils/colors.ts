// Deterministic per-ticker palette.
//
// We compute a hash from the ticker string and pick from a curated set so
// the same ticker always gets the same hue across the app (donut, treemap,
// scatter, drift bars).

const PALETTE = [
  '#4C9BE8',
  '#F4845F',
  '#56C5A2',
  '#B57BEE',
  '#F4C842',
  '#E87A7A',
  '#7AB8E3',
  '#FF9F40',
  '#A5DD9B',
  '#D8A4F4',
  '#FFD480',
  '#F09595',
];

export function tickerColor(ticker: string): string {
  let hash = 0;
  for (let i = 0; i < ticker.length; i += 1) {
    hash = (hash * 31 + ticker.charCodeAt(i)) >>> 0;
  }
  return PALETTE[hash % PALETTE.length];
}

export function pnlColor(pnlPct: number | null | undefined): string {
  if (pnlPct === null || pnlPct === undefined) {
    return 'rgba(107, 116, 132, 0.35)';
  }
  if (pnlPct >= 0) {
    const intensity = Math.min(0.55, 0.18 + pnlPct * 6);
    return `rgba(74, 222, 128, ${intensity})`;
  }
  const intensity = Math.min(0.55, 0.18 + Math.abs(pnlPct) * 6);
  return `rgba(248, 113, 113, ${intensity})`;
}
