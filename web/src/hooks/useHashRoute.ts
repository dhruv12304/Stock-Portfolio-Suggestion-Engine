// Tiny hash router for #setup, #dashboard, and #reasoning deep links.

import { useEffect, useState } from 'react';

export type Route = 'setup' | 'dashboard' | 'reasoning';

const DEFAULT_ROUTE: Route = 'dashboard';
const KNOWN: Route[] = ['setup', 'dashboard', 'reasoning'];

function parse(): Route {
  const raw = window.location.hash.replace('#', '');
  return (KNOWN as string[]).includes(raw) ? (raw as Route) : DEFAULT_ROUTE;
}

export function useHashRoute(): [Route, (next: Route) => void] {
  const [route, setRoute] = useState<Route>(parse);

  useEffect(() => {
    const handler = () => setRoute(parse());
    window.addEventListener('hashchange', handler);
    return () => window.removeEventListener('hashchange', handler);
  }, []);

  const go = (next: Route) => {
    window.location.hash = next;
  };

  return [route, go];
}
