import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { Topbar } from './Topbar';
import type { StreamState } from '../hooks/useStream';

function renderTopbar(stream: StreamState, activePortfolioId: string | null = null) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <Topbar
        route="dashboard"
        onRouteChange={() => undefined}
        stream={stream}
        activePortfolioId={activePortfolioId}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  // Avoid hitting the real network from the portfolios query.
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(JSON.stringify({ items: [], active_id: null }), { status: 200 }),
    ) as unknown as typeof fetch,
  );
});

describe('Topbar', () => {
  it('shows the empty tape strapline when no portfolio is loaded', () => {
    renderTopbar({
      status: 'idle',
      valuation: null,
      lastError: null,
      lastFrameAt: null,
    });
    expect(
      screen.getByText(/Pick or build a portfolio to start streaming/i),
    ).toBeInTheDocument();
  });

  it('shows the brand and tabs', () => {
    renderTopbar({
      status: 'idle',
      valuation: null,
      lastError: null,
      lastFrameAt: null,
    });
    expect(screen.getByText('QUANTSCOPE')).toBeInTheDocument();
    expect(screen.getByText(/01 · SETUP/)).toBeInTheDocument();
    expect(screen.getByText(/02 · DASHBOARD/)).toBeInTheDocument();
    expect(screen.getByText(/03 · REASONING/)).toBeInTheDocument();
  });

  it('renders a green LED when a live valuation arrived recently', () => {
    const { container } = renderTopbar({
      status: 'live',
      valuation: null,
      lastError: null,
      lastFrameAt: new Date(),
    });
    const led = container.querySelector('.led.led-green');
    expect(led).not.toBeNull();
  });

  it('renders an amber LED while reconnecting', () => {
    const { container } = renderTopbar({
      status: 'reconnecting',
      valuation: null,
      lastError: null,
      lastFrameAt: null,
    });
    expect(container.querySelector('.led.led-amber')).not.toBeNull();
  });

  it('renders a red LED on terminal error', () => {
    const { container } = renderTopbar({
      status: 'error',
      valuation: null,
      lastError: 'portfolio_deleted',
      lastFrameAt: null,
    });
    expect(container.querySelector('.led.led-red')).not.toBeNull();
  });
});
