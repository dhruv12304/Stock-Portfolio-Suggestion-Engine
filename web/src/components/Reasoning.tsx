// Reasoning screen — generated insights + factor heatmap + strategy mix +
// drift bars. Selection details are aggregated per ticker, so a holding
// that appears in both strategies is explained in one combined row.

import { useMemo } from 'react';

import { useStrategies } from '../hooks/usePortfolios';
import type { SelectionDetail, Valuation } from '../types';
import { tickerColor } from '../utils/colors';
import { formatMoney } from '../utils/format';

interface Props {
  valuation: Valuation | null;
}

const FACTOR_NAMES = [
  'momentum',
  'growth',
  'stability',
  'quality',
  'value',
  'liquidity',
  'thematic_bonus',
  'etf_preference',
] as const;

const FACTOR_LABELS: Record<(typeof FACTOR_NAMES)[number], string> = {
  momentum: 'Momentum',
  growth: 'Growth',
  stability: 'Stability',
  quality: 'Quality',
  value: 'Value',
  liquidity: 'Liquidity',
  thematic_bonus: 'Theme',
  etf_preference: 'ETF',
};

export function Reasoning({ valuation }: Props) {
  const strategiesQuery = useStrategies();

  if (!valuation) {
    return (
      <div className="placeholder">
        <h2>No active portfolio</h2>
        <p>Build or activate a portfolio first.</p>
      </div>
    );
  }

  const details = valuation.selection_details;
  return (
    <div className="reasoning" id="reasoning">
      <div className="reason-grid">
        <div>
          <InsightCards valuation={valuation} details={details} />
          <RiskReturnMapExplainer />
          <FactorHeatmap details={details} />
        </div>
        <div>
          <StrategyMix
            strategies={valuation.strategies}
            weights={valuation.strategy_weights}
            descriptions={strategiesQuery.data ?? []}
          />
          <DriftBars valuation={valuation} />
          <Exports valuation={valuation} />
        </div>
      </div>
    </div>
  );
}

function InsightCards({ valuation, details }: { valuation: Valuation; details: SelectionDetail[] }) {
  const funded = useMemo(() => valuation.rows.filter((r) => r.shares > 0), [valuation.rows]);
  const etfWeight = funded
    .filter((r) => r.is_etf)
    .reduce((s, r) => s + r.weight, 0);
  const topPick = funded[0];
  const top3 = funded.slice(0, 3).reduce((s, r) => s + r.weight, 0);
  const concentration = top3 >= 0.65 ? 'high concentration' : 'balanced concentration';
  const topConviction = details.slice().sort((a, b) => b.score - a.score)[0];

  return (
    <div className="insights">
      <InsightCard
        head="PORTFOLIO THESIS"
        body={
          <>
            The allocation reflects a <strong>{concentration}</strong>: top 3 holdings
            represent <span className="acc">{(top3 * 100).toFixed(1)}%</span> of deployed
            weights across <strong>{funded.length} funded positions</strong>.
          </>
        }
      />
      <InsightCard
        head="DIVERSIFICATION POSTURE"
        body={
          <>
            ETF exposure contributes approximately{' '}
            <strong>{(etfWeight * 100).toFixed(1)}%</strong> of the blended allocation,
            which helps reduce single-name concentration.
          </>
        }
      />
      <InsightCard
        head="TOP CONVICTION"
        body={
          topConviction ? (
            <>
              <strong>{topConviction.ticker}</strong> · composite score{' '}
              <span className="acc">{topConviction.score.toFixed(2)}</span>. Primary
              drivers: {topConviction.top_factors}.
            </>
          ) : (
            'No reasoning details for this portfolio.'
          )
        }
      />
      <InsightCard
        head="HEADLINE POSITION"
        body={
          topPick ? (
            <>
              <strong>{topPick.ticker}</strong> is the highest-weight funded position at{' '}
              <span className="acc">{(topPick.weight * 100).toFixed(1)}%</span> with{' '}
              <strong>{formatMoney(topPick.current_value)}</strong> currently allocated.
            </>
          ) : (
            'No funded positions.'
          )
        }
      />
    </div>
  );
}

function RiskReturnMapExplainer() {
  return (
    <section className="risk-return-explainer" aria-labelledby="risk-return-explainer-h">
      <h2 id="risk-return-explainer-h" className="risk-return-explainer-h">
        Risk / return map (Dashboard)
      </h2>
      <div className="risk-return-explainer-body">
        <ul>
          <li>
            The <strong>annualized RISK / RETURN</strong> scatter is a <strong>per-holding</strong>{' '}
            snapshot for the same <strong>trading-day window</strong> you select under Equity
            History on the Dashboard.
          </li>
          <li>
            The <strong>Sharpe card is separate</strong>: its <strong>3M / 6M / 1Y / 2Y</strong>{' '}
            buttons choose an independent lookback so Sharpe can stay reportable even when the
            chart is on a shorter range.
          </li>
          <li>
            <strong>Each bubble</strong> is one <strong>funded ticker</strong> with enough price
            history in the active
            portfolio. <strong>Bubble size</strong> scales roughly with its{' '}
            <strong>allocation weight</strong>.
          </li>
          <li>
            <strong>Data:</strong> daily close prices → simple daily returns{' '}
            <code className="risk-return-code">pct_change</code>.
          </li>
          <li>
            <strong>Horizontal axis (volatility):</strong> sample standard deviation of those daily
            returns, annualized as{' '}
            <code className="risk-return-code">σ_annual = std(daily) × √252</code>.
          </li>
          <li>
            <strong>Vertical axis (return):</strong> mean of those daily returns, annualized as{' '}
            <code className="risk-return-code">μ_annual = mean(daily) × 252</code>. This is an
            average-drift shorthand—it is <strong>not</strong> the same as total realized return over
            the window.
          </li>
          <li>
            For <strong>very short windows</strong>, the scatter plot <strong>will be omitted</strong>.
          </li>
        </ul>
      </div>
    </section>
  );
}

function InsightCard({ head, body }: { head: string; body: React.ReactNode }) {
  return (
    <div className="insight">
      <div className="insight-h">{head}</div>
      <div className="insight-b">{body}</div>
    </div>
  );
}

function FactorHeatmap({ details }: { details: SelectionDetail[] }) {
  if (details.length === 0) {
    return null;
  }
  const sorted = [...details].sort(
    (a, b) => (b.blended_weight ?? b.weight) - (a.blended_weight ?? a.weight),
  );
  return (
    <div className="panel">
      <header className="panel-h">
        <span>FACTOR HEATMAP</span>
        <span className="panel-badge">Z-NORMALIZED</span>
      </header>
      <div className="panel-b">
        <table className="factor-table">
          <thead>
            <tr>
              <th>TICKER</th>
              {FACTOR_NAMES.map((f) => (
                <th key={f}>{FACTOR_LABELS[f]}</th>
              ))}
              <th>SCORE</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((row) => (
              <tr key={row.ticker}>
                <td className="tk">{row.ticker}</td>
                {FACTOR_NAMES.map((f) => {
                  const raw = (row as Record<string, unknown>)[f];
                  const value = typeof raw === 'number' ? raw : 0.5;
                  return (
                    <td key={f}>
                      <span className="factor-cell" style={{ background: factorColor(value) }}>
                        {value.toFixed(2)}
                      </span>
                    </td>
                  );
                })}
                <td>
                  <span className="factor-cell factor-cell-score">{row.score.toFixed(2)}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function factorColor(value: number): string {
  if (value > 0.7) return `rgba(74, 222, 128, ${value * 0.6})`;
  if (value > 0.5) return `rgba(34, 211, 238, ${value * 0.45})`;
  if (value > 0.35) return `rgba(250, 204, 21, ${value * 0.45})`;
  return `rgba(248, 113, 113, ${(1 - value) * 0.5})`;
}

function StrategyMix({
  strategies,
  weights,
  descriptions,
}: {
  strategies: string[];
  weights: number[];
  descriptions: Array<{ name: string; description: string }>;
}) {
  return (
    <div className="panel">
      <header className="panel-h">
        <span>STRATEGY MIX</span>
        <span className="panel-badge">USER-SELECTED</span>
      </header>
      <div className="panel-b">
        <div className="strategy-bar">
          {strategies.map((name, i) => (
            <div
              key={name}
              className="strategy-bar-segment"
              style={{
                width: `${(weights[i] ?? 1 / strategies.length) * 100}%`,
                background: i === 0 ? '#22d3ee' : '#a78bfa',
              }}
            />
          ))}
        </div>
        <ul className="strategy-legend">
          {strategies.map((name, i) => {
            const desc = descriptions.find((d) => d.name === name)?.description;
            const sw = i === 0 ? '#22d3ee' : '#a78bfa';
            return (
              <li key={name}>
                <span className="sw" style={{ background: sw }} />
                <strong>{name}</strong>
                {' · '}
                <span className="num">{((weights[i] ?? 1 / strategies.length) * 100).toFixed(0)}%</span>
                {desc && <div className="muted small">{desc}</div>}
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

function DriftBars({ valuation }: { valuation: Valuation }) {
  const rows = valuation.rows.filter((r) => r.shares > 0 && r.current_value !== null);
  const totalValue = rows.reduce((s, r) => s + (r.current_value ?? 0), 0);
  if (totalValue <= 0) return null;
  const max = Math.max(...rows.map((r) => Math.max(r.weight, (r.current_value ?? 0) / totalValue)));

  return (
    <div className="panel">
      <header className="panel-h">
        <span>TARGET vs CURRENT DRIFT</span>
      </header>
      <div className="panel-b">
        {rows.map((row) => {
          const target = row.weight;
          const current = (row.current_value ?? 0) / totalValue;
          const drift = current - target;
          const color = tickerColor(row.ticker);
          return (
            <div key={row.ticker} className="drift-row">
              <div className="drift-ticker" style={{ color }}>
                {row.ticker}
              </div>
              <div className="drift-bar">
                <div
                  className="drift-target"
                  style={{ width: `${(target / max) * 100}%` }}
                />
                <div
                  className="drift-current"
                  style={{
                    width: `${(current / max) * 100}%`,
                    background: color,
                    opacity: 0.6,
                  }}
                />
                <div className="drift-marker" style={{ left: `${(target / max) * 100}%` }} />
              </div>
              <div className="drift-target-label">{(target * 100).toFixed(1)}%</div>
              <div className={'drift-delta ' + (drift >= 0 ? 'pos' : 'neg')}>
                {(drift >= 0 ? '+' : '') + (drift * 100).toFixed(1)}%
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Exports({ valuation }: { valuation: Valuation }) {
  const holdingsCsv = useMemo(() => valuationRowsToCsv(valuation), [valuation]);
  const reasoningCsv = useMemo(() => detailsToCsv(valuation.selection_details), [valuation]);

  return (
    <div className="panel">
      <header className="panel-h">
        <span>EXPORTS</span>
      </header>
      <div className="panel-b panel-b-tight">
        <ExportRow label="holdings.csv" csv={holdingsCsv} />
        <ExportRow label="reasoning.csv" csv={reasoningCsv} />
      </div>
    </div>
  );
}

function ExportRow({ label, csv }: { label: string; csv: string }) {
  const onDownload = () => {
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = label;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };
  return (
    <button type="button" className="export-row" onClick={onDownload}>
      <span className="acc">⬇</span>
      <span>{label}</span>
    </button>
  );
}

function valuationRowsToCsv(v: Valuation): string {
  const header = [
    'ticker',
    'name',
    'shares',
    'weight',
    'allocated',
    'initial_price',
    'current_price',
    'actual_cost',
    'current_value',
    'unrealized_pnl',
    'unrealized_pnl_pct',
    'quote_source',
    'sector',
    'is_etf',
  ];
  const body = v.rows.map((row) =>
    [
      row.ticker,
      JSON.stringify(row.name),
      row.shares,
      row.weight.toFixed(6),
      row.allocated.toFixed(2),
      row.initial_price.toFixed(4),
      row.current_price ?? '',
      row.actual_cost.toFixed(2),
      row.current_value !== null ? row.current_value.toFixed(2) : '',
      row.unrealized_pnl !== null ? row.unrealized_pnl.toFixed(2) : '',
      row.unrealized_pnl_pct !== null ? row.unrealized_pnl_pct.toFixed(6) : '',
      row.quote_source,
      JSON.stringify(row.sector),
      row.is_etf,
    ].join(','),
  );
  return [header.join(','), ...body].join('\n');
}

function detailsToCsv(details: SelectionDetail[]): string {
  const header = [
    'ticker',
    'name',
    'sector',
    'is_etf',
    'score',
    'blended_weight',
    'selection_reason',
    'source_strategies',
    'top_factors',
  ];
  const body = details.map((d) =>
    [
      d.ticker,
      JSON.stringify(d.name),
      JSON.stringify(d.sector),
      d.is_etf,
      d.score.toFixed(4),
      (d.blended_weight ?? d.weight).toFixed(6),
      JSON.stringify(d.selection_reason),
      JSON.stringify((d.source_strategies ?? [d.strategy]).join('+')),
      JSON.stringify(d.top_factors ?? ''),
    ].join(','),
  );
  return [header.join(','), ...body].join('\n');
}
