// Donut chart of holdings by current_value, with click-to-select that
// stays in sync with the treemap and holdings table.

import { useMemo } from 'react';

import type { HoldingRow } from '../types';
import { tickerColor } from '../utils/colors';

interface Props {
  rows: HoldingRow[];
  size?: number;
  innerRadius?: number;
  selected: string | null;
  onSelect: (ticker: string | null) => void;
}

interface Slice {
  ticker: string;
  path: string;
  color: string;
}

function arcPath(cx: number, cy: number, r: number, ri: number, start: number, end: number): string {
  const x0 = cx + r * Math.cos(start);
  const y0 = cy + r * Math.sin(start);
  const x1 = cx + r * Math.cos(end);
  const y1 = cy + r * Math.sin(end);
  const xi0 = cx + ri * Math.cos(end);
  const yi0 = cy + ri * Math.sin(end);
  const xi1 = cx + ri * Math.cos(start);
  const yi1 = cy + ri * Math.sin(start);
  const large = end - start > Math.PI ? 1 : 0;
  return [
    `M ${x0} ${y0}`,
    `A ${r} ${r} 0 ${large} 1 ${x1} ${y1}`,
    `L ${xi0} ${yi0}`,
    `A ${ri} ${ri} 0 ${large} 0 ${xi1} ${yi1}`,
    'Z',
  ].join(' ');
}

export function Donut({ rows, size = 180, innerRadius = 56, selected, onSelect }: Props) {
  const cx = size / 2;
  const cy = size / 2;
  const r = size / 2 - 6;

  const slices = useMemo<Slice[]>(() => {
    const funded = rows.filter((row) => (row.current_value ?? 0) > 0);
    const total = funded.reduce((s, row) => s + (row.current_value ?? 0), 0);
    if (total === 0) return [];

    let angle = -Math.PI / 2;
    return funded.map((row) => {
      const value = (row.current_value ?? 0) / total;
      const next = angle + value * Math.PI * 2;
      const slice: Slice = {
        ticker: row.ticker,
        path: arcPath(cx, cy, r, innerRadius, angle, next),
        color: tickerColor(row.ticker),
      };
      angle = next;
      return slice;
    });
  }, [rows, cx, cy, r, innerRadius]);

  if (slices.length === 0) {
    return (
      <svg width={size} height={size}>
        <text x={cx} y={cy + 4} textAnchor="middle" fill="var(--text-muted)" fontSize="11">
          no funded positions
        </text>
      </svg>
    );
  }

  return (
    <svg width={size} height={size}>
      {slices.map((slice) => (
        <path
          key={slice.ticker}
          d={slice.path}
          fill={slice.color}
          opacity={selected && selected !== slice.ticker ? 0.25 : 1}
          stroke={selected === slice.ticker ? '#e8eef5' : 'var(--panel-2)'}
          strokeWidth={selected === slice.ticker ? 1.5 : 1}
          style={{ cursor: 'pointer', transition: 'opacity 0.15s' }}
          onClick={() => onSelect(selected === slice.ticker ? null : slice.ticker)}
        />
      ))}
      <text
        x={cx}
        y={cy - 4}
        textAnchor="middle"
        fill="var(--text-muted)"
        fontSize="9"
        fontFamily="JetBrains Mono"
        letterSpacing="0.1em"
      >
        HOLDINGS
      </text>
      <text
        x={cx}
        y={cy + 12}
        textAnchor="middle"
        fill="#e8eef5"
        fontSize="16"
        fontWeight={600}
        fontFamily="JetBrains Mono"
      >
        {slices.length}
      </text>
    </svg>
  );
}
