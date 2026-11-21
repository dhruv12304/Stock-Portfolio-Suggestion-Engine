// Thin REST client. The dev server proxies /api -> http://localhost:8000
// (see vite.config.ts), so callers can use absolute /api/v1 paths in both
// development and production.

import type {
  BuildRequest,
  HistoryPayload,
  Period,
  PortfolioList,
  PortfolioMeta,
  RiskPayload,
  SharpeWindow,
  StrategyDescriptor,
  Valuation,
} from './types';

const API_PREFIX = '/api/v1';

async function request<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(API_PREFIX + path, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const detail = await safeError(response);
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

async function safeError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string };
    return body.detail ?? response.statusText;
  } catch {
    return response.statusText;
  }
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = 'ApiError';
  }
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------
export const api = {
  listStrategies: () => request<StrategyDescriptor[]>('/strategies'),

  listPortfolios: () => request<PortfolioList>('/portfolios'),

  buildPortfolio: (payload: BuildRequest) =>
    request<Valuation>('/portfolios/build', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  getPortfolio: (id: string) => request<Valuation>(`/portfolios/${id}`),

  renamePortfolio: (id: string, name: string) =>
    request<PortfolioMeta>(`/portfolios/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ name }),
    }),

  duplicatePortfolio: (id: string, name?: string) =>
    request<PortfolioMeta>(`/portfolios/${id}/duplicate`, {
      method: 'POST',
      body: JSON.stringify(name ? { name } : {}),
    }),

  activatePortfolio: (id: string) =>
    request<void>(`/portfolios/${id}/activate`, { method: 'POST' }),

  deletePortfolio: (id: string) =>
    request<void>(`/portfolios/${id}`, { method: 'DELETE' }),

  getHistory: (id: string, period: Period) =>
    request<HistoryPayload>(`/portfolios/${id}/history?period=${period}`),

  getRisk: (id: string, period: Period) =>
    request<RiskPayload>(`/portfolios/${id}/risk?period=${period}`),
};

export function streamUrl(portfolioId: string): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}${API_PREFIX}/portfolios/${portfolioId}/stream`;
}

export { SharpeWindow };
