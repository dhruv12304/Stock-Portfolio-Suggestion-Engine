// Type contracts mirroring api/service/schemas.py.
//
// Kept in sync by hand for Phase 4. A future iteration can swap this for a
// generated OpenAPI types file (e.g. via openapi-typescript) if the API
// surface grows.

export type Period = '5d' | '1mo' | '3mo' | '6mo' | '1y' | '2y';
export type SharpeWindow = '3m' | '6m' | '1y' | '2y';
export type QuoteSource = 'live' | 'close_fallback' | 'unavailable';

export interface StrategyDescriptor {
  name: string;
  description: string;
}

export interface HoldingRow {
  ticker: string;
  name: string;
  weight: number;
  shares: number;
  allocated: number;
  initial_price: number;
  current_price: number | null;
  actual_cost: number;
  current_value: number | null;
  unrealized_pnl: number | null;
  unrealized_pnl_pct: number | null;
  quote_source: QuoteSource;
  quote_as_of: string;
  sector: string;
  is_etf: boolean;
}

export interface Summary {
  investment_amount: number;
  total_invested: number;
  leftover_cash: number;
  current_portfolio_value: number;
  total_portfolio_value: number;
  unrealized_pnl: number | null;
  unrealized_pnl_pct: number | null;
}

export interface MarketDataStatus {
  as_of: string;
  any_fallback: boolean;
  any_unavailable: boolean;
  missing: string[];
}

export interface SelectionDetail {
  ticker: string;
  name: string;
  strategy?: string;
  source_strategies?: string[];
  weight: number;
  blended_weight?: number;
  score: number;
  is_etf: boolean;
  sector: string;
  selection_reason: string;
  top_factors: string;
  [factor: string]: unknown;
}

export interface Valuation {
  portfolio_id: string;
  name: string;
  built_at: string;
  investment_amount: number;
  strategies: string[];
  strategy_weights: number[];
  rows: HoldingRow[];
  summary: Summary;
  status: MarketDataStatus;
  selection_details: SelectionDetail[];
}

export interface PortfolioMeta {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
  active: boolean;
}

export interface PortfolioList {
  items: PortfolioMeta[];
  active_id: string | null;
}

export interface BuildRequest {
  name?: string;
  investment_amount: number;
  strategies: string[];
  strategy_weights?: number[];
  save?: boolean;
}

export interface HistoryPoint {
  date: string;
  value: number;
}

export interface HistoryPayload {
  portfolio_id: string;
  period: Period;
  points: HistoryPoint[];
  source: 'recorded' | 'backcast';
  missing: string[];
}

export interface SharpeWindowResult {
  window: SharpeWindow;
  sharpe: number | null;
  annual_return: number | null;
  annual_volatility: number | null;
  samples: number | null;
}

export interface RiskTickerStat {
  ticker: string;
  annual_return: number;
  annual_volatility: number;
  weight: number;
}

export interface RiskPayload {
  portfolio_id: string;
  period: Period;
  risk_free_rate: number;
  sharpe: Record<SharpeWindow, SharpeWindowResult>;
  tickers: RiskTickerStat[];
  correlation_tickers: string[];
  correlation_matrix: number[][];
  note: string | null;
}

export type StreamFrame =
  | { type: 'valuation'; valuation: Valuation }
  | { type: 'error'; message: string }
  | { type: 'ping' };
