// Dashboard screen — KPI strip + Treemap/Donut/Holdings hero + TrendChart + RiskGrid.
//
// Live valuation flows in via the WebSocket stream; the REST query is the
// initial fallback when the stream is not yet connected. The stream's
// valuation always wins once available so the KPI strip and chart's last
// point reflect the current tick.

import { useMemo, useState } from 'react';

import { Donut } from './Donut';
import { Treemap } from './Treemap';
import { TrendChart } from './TrendChart';
import { RiskGrid } from './RiskGrid';
import { useElementSize } from '../hooks/useElementSize';
import { useHistory, usePortfolio, useRisk } from '../hooks/usePortfolios';
import type { StreamState } from '../hooks/useStream';
import type { Period, SharpeWindow, Valuation } from '../types';
import { tickerColor } from '../utils/colors';
import { formatMoney, formatPercent } from '../utils/format';

interface Props {
  portfolioId: string | null;
  stream: StreamState;
}

export function Dashboard({ portfolioId, stream }: Props) {
  const portfolioQuery = usePortfolio(portfolioId);
  const [period, setPeriod] = useState<Period>('1mo');
  const [sharpeWindow, setSharpeWindow] = useState<SharpeWindow>('1y');
  const historyQuery = useHistory(portfolioId, period);
  const riskQuery = useRisk(portfolioId, period);

  const [selected, setSelected] = useState<string | null>(null);

  const valuation = stream.valuation ?? portfolioQuery.data ?? null;

  if (!portfolioId) {
    return (
      <div className="placeholder">
        <h2>No active portfolio</h2>
        <p>Build one from the Setup tab to start streaming live valuations.</p>
      </div>
    );
  }

  if (!valuation) {
    return (
      <div className="placeholder">
        <h2>Loading…</h2>
        <p>Fetching valuation from the backend.</p>
      </div>
    );
  }

  return (
    <div className="dashboard">
      <DataFreshnessBanner valuation={valuation} stream={stream} />
      <KpiStrip valuation={valuation} />
      <HeroPanel valuation={valuation} selected={selected} onSelect={setSelected} />
      <TrendChart
        points={historyQuery.data?.points ?? []}
        source={historyQuery.data?.source ?? 'backcast'}
        range={period}
        setRange={setPeriod}
        livePortfolioValue={valuation.summary.current_portfolio_value}
      />
      <RiskGrid
        risk={riskQuery.data ?? null}
        loading={riskQuery.isFetching}
        sharpeWindow={sharpeWindow}
        setSharpeWindow={setSharpeWindow}
      />
    </div>
  );
}

function DataFreshnessBanner({
  valuation,
  stream,
}: {
  valuation: Valuation;
  stream: StreamState;
}) {
  const anyFallback = valuation.status.any_fallback;
  const anyUnavailable = valuation.status.any_unavailable;
  if (!anyFallback && !anyUnavailable && stream.status === 'live') return null;

  let level: 'info' | 'warn' = 'info';
  let message: string;
  if (stream.status === 'error') {
    level = 'warn';
    message = `Live stream error: ${stream.lastError ?? 'unknown'}.`;
  } else if (stream.status === 'reconnecting' || stream.status === 'connecting') {
    level = 'info';
    message = 'Reconnecting to live data feed…';
  } else if (anyUnavailable) {
    level = 'warn';
    message = `Some holdings have no current price (${valuation.rows.filter((r) => r.quote_source === 'unavailable').length} unavailable). Equity Value and Total Portfolio are partial, and P&L is hidden until prices return.`;
  } else if (anyFallback) {
    level = 'info';
    message = 'Showing last close for some holdings; live quotes are temporarily unavailable.';
  } else {
    return null;
  }
  return <div className={`banner banner-${level}`}>{message}</div>;
}

function KpiStrip({ valuation }: { valuation: Valuation }) {
  const s = valuation.summary;
  const pnlSign = s.unrealized_pnl === null ? '' : s.unrealized_pnl >= 0 ? 'pos' : 'neg';
  const investedPct = s.investment_amount > 0 ? s.total_invested / s.investment_amount : 0;

  return (
    <div className="kpis">
      <Kpi label="Budget" value={formatMoney(s.investment_amount)} sub={`Allocated ${(investedPct * 100).toFixed(1)}%`} />
      <Kpi label="Invested" value={formatMoney(s.total_invested)} sub="Whole-share rounding" />
      <Kpi label="Leftover" value={formatMoney(s.leftover_cash)} sub="Cash" />
      <Kpi label="Equity Value" value={formatMoney(s.current_portfolio_value)} sub="Invested holdings only" />
      <Kpi label="Total Portfolio" value={formatMoney(s.total_portfolio_value)} sub="Equity + cash" />
      <Kpi
        label="Unrealized P&L"
        value={
          <span className={pnlSign}>
            {s.unrealized_pnl === null
              ? '—'
              : (s.unrealized_pnl >= 0 ? '+' : '') + formatMoney(s.unrealized_pnl)}
          </span>
        }
        sub={
          <span className={pnlSign}>
            {s.unrealized_pnl === null ? 'Hidden while quotes are unavailable' : `${formatPercent(s.unrealized_pnl_pct, 2)} since entry`}
          </span>
        }
      />
    </div>
  );
}

function Kpi({
  label,
  value,
  sub,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
}) {
  return (
    <div className="kpi">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      {sub && <div className="kpi-sub">{sub}</div>}
    </div>
  );
}

function HeroPanel({
  valuation,
  selected,
  onSelect,
}: {
  valuation: Valuation;
  selected: string | null;
  onSelect: (next: string | null) => void;
}) {
  const [treemapBoxRef, treemapSize] = useElementSize<HTMLDivElement>();
  const rows = useMemo(
    () => [...valuation.rows].sort((a, b) => (b.current_value ?? -1) - (a.current_value ?? -1)),
    [valuation.rows],
  );

  const tw = treemapSize.width > 48 ? Math.max(200, treemapSize.width - 16) : 534;
  const th = treemapSize.height > 48 ? Math.max(160, treemapSize.height - 16) : 344;

  return (
    <div className="hero">
      <div className="panel">
        <header className="panel-h">
          <span>HOLDINGS · MARKET MAP</span>
          <span className="panel-badge">WEIGHT × P&L</span>
          <span className="panel-spacer" />
          <span className="panel-meta">{rows.filter((r) => r.shares > 0).length} POSITIONS</span>
        </header>
        <div className="panel-b" style={{ padding: 6 }}>
          <div className="treemap-slot" ref={treemapBoxRef}>
            <Treemap rows={rows} width={tw} height={th} selected={selected} onSelect={onSelect} />
          </div>
        </div>
      </div>
      <div className="panel">
        <header className="panel-h">
          <span>ALLOCATION</span>
          <span className="panel-badge">TARGET vs LIVE</span>
        </header>
        <div className="panel-b">
          <div className="alloc-grid">
            <Donut rows={rows} selected={selected} onSelect={onSelect} />
            <div className="alloc-legend">
              {rows
                .filter((row) => row.shares > 0)
                .map((row) => (
                  <button
                    key={row.ticker}
                    type="button"
                    className={
                      'legend-row' + (selected === row.ticker ? ' selected' : '')
                    }
                    onClick={() => onSelect(selected === row.ticker ? null : row.ticker)}
                  >
                    <span
                      className="legend-sw"
                      style={{ background: tickerColor(row.ticker) }}
                    />
                    <span className="legend-tk">{row.ticker}</span>
                    <span className="legend-name">{row.name}</span>
                    <span className="legend-weight">{(row.weight * 100).toFixed(1)}%</span>
                    <span className="legend-value">{formatMoney(row.current_value, 0)}</span>
                  </button>
                ))}
            </div>
          </div>
        </div>
        <div className="holdings-table-wrap">
          <table className="holdings-table">
            <thead>
              <tr>
                <th>TICKER</th>
                <th>SH</th>
                <th className="r">PRICE</th>
                <th className="r">VALUE</th>
                <th className="r">P&L</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const fallback = row.quote_source !== 'live';
                return (
                  <tr
                    key={row.ticker}
                    className={
                      (selected === row.ticker ? 'selected ' : '') +
                      (fallback ? 'fallback' : '')
                    }
                    onClick={() => onSelect(selected === row.ticker ? null : row.ticker)}
                  >
                    <td className="ticker-cell">
                      <span className="tk">{row.ticker}</span>
                      {fallback && <span className="fallback-chip">{row.quote_source}</span>}
                    </td>
                    <td>{row.shares}</td>
                    <td className="r">
                      {row.current_price !== null ? formatMoney(row.current_price, 2) : '—'}
                    </td>
                    <td className="r">{formatMoney(row.current_value, 0)}</td>
                    <td
                      className={
                        'r ' + ((row.unrealized_pnl_pct ?? 0) >= 0 ? 'pos' : 'neg')
                      }
                    >
                      {formatPercent(row.unrealized_pnl_pct, 1)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
