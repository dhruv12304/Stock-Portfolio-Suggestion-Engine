// Squarified treemap of holdings, coloured by per-row P&L.
//
// Cells smaller than a threshold collapse their label so the layout
// stays readable on portfolios with one dominant position.

import { useMemo } from 'react';

import type { HoldingRow } from '../types';
import { pnlColor } from '../utils/colors';
import { formatPercent } from '../utils/format';

interface Props {
  rows: HoldingRow[];
  width: number;
  height: number;
  selected: string | null;
  onSelect: (ticker: string | null) => void;
}

interface Cell {
  ticker: string;
  weight: number;
  pnlPct: number | null;
  x: number;
  y: number;
  w: number;
  h: number;
}

interface Item {
  ticker: string;
  weight: number;
  pnlPct: number | null;
  value: number;
  area: number;
}

function squarify(items: Item[], width: number, height: number): Cell[] {
  const total = items.reduce((s, it) => s + it.value, 0) || 1;
  const normalised = items
    .map((it) => ({ ...it, area: (it.value / total) * width * height }))
    .sort((a, b) => b.area - a.area);

  const out: Cell[] = [];
  let x = 0;
  let y = 0;
  let cw = width;
  let ch = height;
  let row: Item[] = [];

  const worst = (rowItems: Item[], side: number): number => {
    if (!rowItems.length || !side) return Infinity;
    const sum = rowItems.reduce((s, r) => s + r.area, 0);
    const max = Math.max(...rowItems.map((r) => r.area));
    const min = Math.min(...rowItems.map((r) => r.area));
    return Math.max((side * side * max) / (sum * sum), (sum * sum) / (side * side * min));
  };

  const layoutRow = (rowItems: Item[]) => {
    const sum = rowItems.reduce((s, r) => s + r.area, 0);
    if (cw < ch) {
      const rowH = sum / cw;
      let cx = x;
      for (const r of rowItems) {
        const rw = r.area / rowH;
        out.push({
          ticker: r.ticker,
          weight: r.weight,
          pnlPct: r.pnlPct,
          x: cx,
          y,
          w: rw,
          h: rowH,
        });
        cx += rw;
      }
      y += rowH;
      ch -= rowH;
    } else {
      const rowW = sum / ch;
      let cy = y;
      for (const r of rowItems) {
        const rh = r.area / rowW;
        out.push({
          ticker: r.ticker,
          weight: r.weight,
          pnlPct: r.pnlPct,
          x,
          y: cy,
          w: rowW,
          h: rh,
        });
        cy += rh;
      }
      x += rowW;
      cw -= rowW;
    }
  };

  for (const item of normalised) {
    const side = Math.min(cw, ch);
    const before = worst(row, side);
    const after = worst([...row, item], side);
    if (!row.length || after <= before) {
      row.push(item);
    } else {
      layoutRow(row);
      row = [item];
    }
  }
  if (row.length) layoutRow(row);
  return out;
}

export function Treemap({ rows, width, height, selected, onSelect }: Props) {
  const cells = useMemo(() => {
    const items = rows
      .filter((row) => (row.current_value ?? 0) > 0)
      .map<Item>((row) => ({
        ticker: row.ticker,
        weight: row.weight,
        pnlPct: row.unrealized_pnl_pct,
        value: row.current_value ?? 0,
        area: 0,
      }));
    return squarify(items, width, height);
  }, [rows, width, height]);

  if (cells.length === 0) {
    return (
      <div className="treemap-empty" style={{ width, height }}>
        No funded positions yet.
      </div>
    );
  }

  return (
    <div className="treemap" style={{ width: width + 16, height: height + 16 }}>
      <div style={{ position: 'relative', width, height }}>
        {cells.map((c) => {
          const small = c.w < 80 || c.h < 50;
          const tiny = c.w < 60 || c.h < 40;
          const isSelected = selected === c.ticker;
          return (
            <button
              key={c.ticker}
              type="button"
              className={
                'tm-cell' +
                (small ? ' tm-cell-sm' : '') +
                (tiny ? ' tm-cell-xs' : '') +
                (isSelected ? ' tm-cell-selected' : '')
              }
              style={{
                left: c.x,
                top: c.y,
                width: c.w,
                height: c.h,
                background: pnlColor(c.pnlPct),
              }}
              onClick={() => onSelect(isSelected ? null : c.ticker)}
              aria-pressed={isSelected}
            >
              <span className="tm-ticker">{c.ticker}</span>
              {!tiny && <span className="tm-weight">{(c.weight * 100).toFixed(1)}%</span>}
              {!small && (
                <span className={'tm-pnl ' + ((c.pnlPct ?? 0) >= 0 ? 'pos' : 'neg')}>
                  {formatPercent(c.pnlPct, 1)}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
