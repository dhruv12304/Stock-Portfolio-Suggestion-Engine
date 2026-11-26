// Three-column risk panel: Sharpe big-number + windows / Scatter / Correlation.
//
// A window returning null from the backend renders 'N/A' rather than a
// misleading numeric value when the history is too short or too flat.

import type { RiskPayload, SharpeWindow } from '../types';
import { formatPercent } from '../utils/format';
import { formatRiskNote, riskScatterEmptyCopy } from '../utils/riskNotes';

interface Props {
  risk: RiskPayload | null;
  loading: boolean;
  sharpeWindow: SharpeWindow;
  setSharpeWindow: (next: SharpeWindow) => void;
}

const WINDOWS: SharpeWindow[] = ['3m', '6m', '1y', '2y'];

export function RiskGrid({ risk, loading, sharpeWindow, setSharpeWindow }: Props) {
  return (
    <div className="risk-grid">
      <SharpeCard risk={risk} loading={loading} window={sharpeWindow} setWindow={setSharpeWindow} />
      <RiskScatter risk={risk} />
      <CorrelationHeatmap risk={risk} />
    </div>
  );
}

function SharpeCard({
  risk,
  loading,
  window,
  setWindow,
}: {
  risk: RiskPayload | null;
  loading: boolean;
  window: SharpeWindow;
  setWindow: (next: SharpeWindow) => void;
}) {
  const entry = risk?.sharpe[window] ?? null;
  const sharpe = entry?.sharpe ?? null;
  const reportable = sharpe !== null && entry !== null;
  const display = reportable ? sharpe!.toFixed(2) : 'N/A';
  const subtitle = reportable
    ? `${entry!.samples ?? 0} trading days · σ ${formatPercent(entry!.annual_volatility, 1)}`
    : 'insufficient history or flat series';

  const regime = !reportable
    ? '—'
    : sharpe! >= 2
      ? 'Strong risk-adjusted'
      : sharpe! >= 1
        ? 'Good'
        : sharpe! >= 0
          ? 'Below target'
          : 'Worse than risk-free';

  const gaugeFill = reportable ? Math.min(100, Math.max(8, (sharpe! / 3) * 100)) : 0;

  return (
    <div className="risk-panel">
      <header className="risk-h">
        <h3>SHARPE RATIO</h3>
        <span className="risk-badge">
          Rf {risk ? formatPercent(risk.risk_free_rate, 2) : '—'}
        </span>
        <span className="risk-spacer" />
        <div className="range-pills small">
          {WINDOWS.map((w) => (
            <button
              key={w}
              type="button"
              className={window === w ? 'active' : ''}
              onClick={() => setWindow(w)}
            >
              {w.toUpperCase()}
            </button>
          ))}
        </div>
      </header>
      <div className="risk-body">
        <div className="sharpe-num">{loading ? '…' : display}</div>
        <div className="sharpe-gauge">
          <div className="sharpe-gauge-fill" style={{ width: `${gaugeFill}%` }} />
        </div>
        <dl className="sharpe-meta">
          <div>
            <dt>REGIME</dt>
            <dd>{regime}</dd>
          </div>
          <div>
            <dt>WINDOW</dt>
            <dd>{subtitle}</dd>
          </div>
          <div>
            <dt>SCOPE</dt>
            <dd>Independent of Equity History</dd>
          </div>
          {risk?.note && risk.note !== 'insufficient_history' && (
            <div>
              <dt>NOTE</dt>
              <dd>{formatRiskNote(risk.note)}</dd>
            </div>
          )}
        </dl>
      </div>
    </div>
  );
}

function RiskScatterFootnote() {
  return (
    <p className="risk-scatter-footnote">
      How to read this plot (math and caveats) → see{' '}
      <a href="#reasoning">03 · REASONING</a>.
    </p>
  );
}

function RiskScatter({ risk }: { risk: RiskPayload | null }) {
  const data = risk?.tickers ?? [];
  const insufficient = !risk || risk.note === 'insufficient_history' || data.length === 0;

  if (insufficient) {
    return (
      <div className="risk-panel">
        <header className="risk-h">
          <h3>RISK / RETURN</h3>
          <span className="risk-badge">ANNUALIZED</span>
        </header>
        <div className="risk-body risk-scatter-body">
          <div className="risk-empty">{riskScatterEmptyCopy(risk?.note)}</div>
          <RiskScatterFootnote />
        </div>
      </div>
    );
  }

  const w = 320;
  const h = 240;
  const padL = 40;
  const padB = 28;
  const padR = 8;
  const padT = 10;

  const xs = data.map((d) => d.annual_volatility);
  const ys = data.map((d) => d.annual_return);
  const xMax = Math.max(...xs, 0.01) * 1.15;
  const yMax = Math.max(...ys, 0.01) * 1.15;
  const yMin = Math.min(...ys, 0) * 1.15;
  const sx = (v: number) => padL + (v / xMax) * (w - padL - padR);
  const sy = (v: number) => padT + (h - padT - padB) * (1 - (v - yMin) / (yMax - yMin || 1));

  return (
    <div className="risk-panel">
      <header className="risk-h">
        <h3>RISK / RETURN</h3>
        <span className="risk-badge">{risk?.period?.toUpperCase()} ANNUALIZED</span>
      </header>
      <div className="risk-body risk-scatter-body">
        <svg width={w} height={h}>
          {[0.25, 0.5, 0.75].map((t) => (
            <line
              key={t}
              x1={padL}
              x2={w - padR}
              y1={padT + (h - padT - padB) * t}
              y2={padT + (h - padT - padB) * t}
              stroke="var(--chart-grid)"
              strokeWidth={1}
              strokeDasharray="4 6"
            />
          ))}
          <text
            x={padL}
            y={h - 4}
            fontSize="9"
            fontFamily="JetBrains Mono"
            fill="var(--text-muted)"
          >
            VOLATILITY →
          </text>
          <text
            x={6}
            y={padT + 6}
            fontSize="9"
            fontFamily="JetBrains Mono"
            fill="var(--text-muted)"
          >
            ↑ RETURN
          </text>
          {data.map((d) => {
            const r = 6 + d.weight * 30;
            const fillOpacity = 0.32 + Math.min(0.48, d.weight * 1.1);
            return (
              <g key={d.ticker}>
                <circle
                  cx={sx(d.annual_volatility)}
                  cy={sy(d.annual_return)}
                  r={r}
                  fill="#22d3ee"
                  fillOpacity={fillOpacity}
                  stroke="#22d3ee"
                  strokeOpacity={0.85}
                  strokeWidth={1.25}
                />
                <text
                  x={sx(d.annual_volatility)}
                  y={sy(d.annual_return) + 3}
                  textAnchor="middle"
                  fontSize="9"
                  fontFamily="JetBrains Mono"
                  fontWeight={700}
                  fill="#e8eef5"
                  stroke="#0a0e14"
                  strokeWidth={2.4}
                  strokeLinejoin="round"
                  paintOrder="stroke fill"
                >
                  {d.ticker}
                </text>
              </g>
            );
          })}
        </svg>
        <RiskScatterFootnote />
      </div>
    </div>
  );
}

function CorrelationHeatmap({ risk }: { risk: RiskPayload | null }) {
  if (risk?.note === 'insufficient_history') {
    return (
      <div className="risk-panel">
        <header className="risk-h">
          <h3>CORRELATION</h3>
        </header>
        <div className="risk-body risk-scatter-body">
          <div className="risk-empty">{formatRiskNote(risk.note)}</div>
          <RiskScatterFootnote />
        </div>
      </div>
    );
  }
  const tickers = risk?.correlation_tickers ?? [];
  const matrix = risk?.correlation_matrix ?? [];
  if (tickers.length < 2 || matrix.length === 0) {
    return (
      <div className="risk-panel">
        <header className="risk-h">
          <h3>CORRELATION</h3>
        </header>
        <div className="risk-empty">Need at least two funded tickers to compute correlation.</div>
      </div>
    );
  }
  const cell = 30;
  const pad = 36;
  const w = pad + tickers.length * cell + 4;
  const h = pad + tickers.length * cell + 4;

  return (
    <div className="risk-panel">
      <header className="risk-h">
        <h3>CORRELATION</h3>
        <span className="risk-badge">PEARSON · {risk!.period.toUpperCase()}</span>
      </header>
      <div className="risk-body">
        <svg width={w} height={h}>
          {tickers.map((t, i) => (
            <text
              key={`x${t}`}
              x={pad + i * cell + cell / 2}
              y={pad - 8}
              textAnchor="middle"
              fontSize="9"
              fontFamily="JetBrains Mono"
              fill="var(--text-dim)"
            >
              {t}
            </text>
          ))}
          {tickers.map((t, j) => (
            <text
              key={`y${t}`}
              x={pad - 8}
              y={pad + j * cell + cell / 2 + 3}
              textAnchor="end"
              fontSize="9"
              fontFamily="JetBrains Mono"
              fill="var(--text-dim)"
            >
              {t}
            </text>
          ))}
          {matrix.map((row, i) =>
            row.map((v, j) => {
              const opacity = Math.max(0.1, Math.min(0.95, Math.abs(v)));
              const fill =
                v > 0.5
                  ? '#22d3ee'
                  : v > 0
                    ? '#0e7490'
                    : v > -0.5
                      ? '#7f1d1d'
                      : '#f87171';
              return (
                <g key={`${i}-${j}`}>
                  <rect
                    x={pad + j * cell + 1}
                    y={pad + i * cell + 1}
                    width={cell - 2}
                    height={cell - 2}
                    fill={fill}
                    opacity={opacity}
                  />
                  <text
                    x={pad + j * cell + cell / 2}
                    y={pad + i * cell + cell / 2 + 3}
                    textAnchor="middle"
                    fontSize="9"
                    fontFamily="JetBrains Mono"
                    fill={opacity > 0.55 ? '#0a0e14' : '#9ca8b8'}
                  >
                    {v.toFixed(2)}
                  </text>
                </g>
              );
            }),
          )}
        </svg>
      </div>
    </div>
  );
}
