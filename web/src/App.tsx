// App shell. Wires together topbar, hash routing, REST queries, and the
// live valuation stream. The active portfolio is whatever the backend
// flags as active in the snapshots index; once a portfolio is built or
// activated, the topbar's LED + tape become live and the dashboard pulls
// snapshots from the stream.

import { Topbar } from './components/Topbar';
import { Setup } from './components/Setup';
import { Dashboard } from './components/Dashboard';
import { Reasoning } from './components/Reasoning';
import { useHashRoute } from './hooks/useHashRoute';
import { usePortfolio, usePortfolioList } from './hooks/usePortfolios';
import { useStream } from './hooks/useStream';

import './styles.css';

export function App() {
  const [route, setRoute] = useHashRoute();
  const portfolios = usePortfolioList();
  const activePortfolioId = portfolios.data?.active_id ?? null;

  const stream = useStream(activePortfolioId);
  const portfolioQuery = usePortfolio(activePortfolioId);
  const valuation = stream.valuation ?? portfolioQuery.data ?? null;

  return (
    <div className="app">
      <Topbar
        route={route}
        onRouteChange={setRoute}
        stream={stream}
        activePortfolioId={activePortfolioId}
        valuation={valuation}
      />
      <main className="route">
        {route === 'setup' && <Setup onBuilt={() => setRoute('dashboard')} />}
        {route === 'dashboard' && (
          <Dashboard portfolioId={activePortfolioId} stream={stream} />
        )}
        {route === 'reasoning' && <Reasoning valuation={valuation} />}
      </main>
    </div>
  );
}
