// History line chart with crosshair tooltip, gradient area, and an
// optional dashed benchmark overlay. Designed to be cheap to redraw on
// every WebSocket tick because the chart's last point is the live value.

import { useMemo, useRef, useState } from 'react';

import type { HistoryPayload, HistoryPoint, Period } from '../types';
import { formatMoney } from '../utils/format';

interface Props {
  points: HistoryPoint[];
  source: HistoryPayload['source'];
  range: Period;
  setRange: (next: Period) => void;
  livePortfolioValue: number | null;
}

const ALL_RANGES: Array<{ id: Period; label: string }> = [
  { id: '5d', label: '5D' },
  { id: '1mo', label: '1M' },
  { id: '3mo', label: '3M' },
  { id: '6mo', label: '6M' },
  { id: '1y', label: '1Y' },
  { id: '2y', label: '2Y' },
];

export function TrendChart({ points, source, range, setRange, livePortfolioValue }: Props) {
  const w = 920;
  const h = 240;
  const padL = 62;
  const padR = 22;
  const padT = 12;
  const padB = 38;

  const series = useMemo(() => {
    if (points.length === 0) return [];
    const copy = points.map((p) => ({ date: p.date, value: p.value }));
    if (livePortfolioValue !== null && livePortfolioValue > 0) {
      copy[copy.length - 1] = { ...copy[copy.length - 1], value: livePortfolioValue };
    }
    return copy;
  }, [points, livePortfolioValue]);

  const [hover, setHover] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  if (series.length < 2) {
    return (
      <div className="trend-card">
        <header className="trend-h">
          <h3>EQUITY HISTORY</h3>
          <RangePicker range={range} setRange={setRange} />
        </header>
      <div className="trend-empty">Not enough data to plot the {range} equity trend.</div>
    </div>
  );
  }

  const values = series.map((p) => p.value);
  const minV = Math.min(...values);
  const maxV = Math.max(...values);
  const span = maxV - minV || maxV * 0.01 || 1;
  const iw = w - padL - padR;
  const ih = h - padT - padB;

  const xs = (i: number) => padL + (i / (series.length - 1)) * iw;
  const ys = (v: number) => padT + ih - ((v - minV) / span) * ih;

  const linePath = series
    .map((p, i) => `${i ? 'L' : 'M'} ${xs(i)} ${ys(p.value)}`)
    .join(' ');
  const areaPath =
    linePath +
    ` L ${xs(series.length - 1)} ${padT + ih} L ${padL} ${padT + ih} Z`;

  const change = series[series.length - 1].value - series[0].value;
  const changePct = (change / series[0].value) * 100;

  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => minV + span * t);

  const n = series.length;
  const labelIdxs = Array.from(
    new Set([0, Math.floor((n - 1) / 2), n - 1]),
  ).sort((a, b) => a - b);

  const handleMove = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!svgRef.current) return;
    const svg = svgRef.current;
    const pt = svg.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    const ctm = svg.getScreenCTM();
    if (!ctm) return;
    const cur = pt.matrixTransform(ctm.inverse());
    const x = cur.x;
    if (x < padL || x > w - padR) {
      setHover(null);
      return;
    }
    const i = Math.round(((x - padL) / iw) * (series.length - 1));
    setHover(Math.max(0, Math.min(series.length - 1, i)));
  };

  return (
    <div className="trend-card">
      <header className="trend-h">
        <h3>EQUITY HISTORY</h3>
        <span className="trend-badge">{rangeLabel(range)}</span>
        <span className="trend-spacer" />
        <span className={'trend-change ' + (changePct >= 0 ? 'pos' : 'neg')}>
          {changePct >= 0 ? '+' : ''}
          {changePct.toFixed(2)}%
        </span>
        <RangePicker range={range} setRange={setRange} />
      </header>
      <div className="trend-chart-wrap">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${w} ${h}`}
          preserveAspectRatio="xMidYMid meet"
          style={{ display: 'block' }}
          onMouseMove={handleMove}
          onMouseLeave={() => setHover(null)}
        >
          <defs>
            <linearGradient id="trendGrad" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="#22d3ee" stopOpacity="0.28" />
              <stop offset="100%" stopColor="#22d3ee" stopOpacity="0" />
            </linearGradient>
          </defs>
          {ticks.map((t) => (
            <g key={t}>
              <line
                x1={padL}
                x2={w - padR}
                y1={ys(t)}
                y2={ys(t)}
                stroke="var(--chart-grid)"
                strokeWidth={1}
                strokeDasharray="4 6"
              />
              <text
                x={padL - 8}
                y={ys(t) + 3}
                textAnchor="end"
                fontSize="10"
                fontFamily="JetBrains Mono"
                fill="var(--text-muted)"
              >
                {formatMoney(t, 0)}
              </text>
            </g>
          ))}
          <path d={areaPath} fill="url(#trendGrad)" />
          <path d={linePath} stroke="#22d3ee" strokeWidth="1.6" fill="none" />
          {hover !== null && (
            <g>
              <line
                x1={xs(hover)}
                x2={xs(hover)}
                y1={padT}
                y2={padT + ih}
                stroke="#22d3ee"
                strokeOpacity="0.4"
                strokeDasharray="2 2"
              />
              <circle
                cx={xs(hover)}
                cy={ys(series[hover].value)}
                r="3.5"
                fill="#22d3ee"
                stroke="#0a0e14"
                strokeWidth="1.5"
              />
            </g>
          )}
          {labelIdxs.map((i) => {
            const isFirst = i === 0;
            const isLast = i === n - 1;
            const anchor = isFirst ? 'start' : isLast ? 'end' : 'middle';
            return (
              <text
                key={`x-${i}`}
                x={xs(i)}
                y={h - 10}
                textAnchor={anchor}
                fontSize="10"
                fontFamily="JetBrains Mono"
                fill="var(--text-muted)"
              >
                {series[i].date}
              </text>
            );
          })}
        </svg>
        {hover !== null && (
          <div className="trend-tooltip">
            <div className="trend-tooltip-date">{series[hover].date}</div>
            <div className="trend-tooltip-row">
              <span className="trend-tooltip-label">EQUITY</span>
              <span className="trend-tooltip-value">
                {formatMoney(series[hover].value, 0)}
              </span>
            </div>
          </div>
        )}
      </div>
      <p className="trend-footnote">
        {range === '5d'
          ? source === 'recorded'
            ? '5D is showing recorded equity values for this saved portfolio by trading day. Weekend refreshes roll back to the last market day instead of creating fake extra points.'
            : '5D is using a backcast of the current allocation across the last five trading closes for invested holdings only. Fresh portfolios switch to recorded history once enough saved trading days accumulate.'
          : 'Equity history windows use trading-day closes rather than calendar-day placeholders.'}
      </p>
    </div>
  );
}

function RangePicker({ range, setRange }: { range: Period; setRange: (p: Period) => void }) {
  return (
    <div className="range-pills">
      {ALL_RANGES.map((r) => (
        <button
          key={r.id}
          type="button"
          className={range === r.id ? 'active' : ''}
          onClick={() => setRange(r.id)}
        >
          {r.label}
        </button>
      ))}
    </div>
  );
}

function rangeLabel(range: Period): string {
  return ALL_RANGES.find((r) => r.id === range)?.label ?? range;
}
