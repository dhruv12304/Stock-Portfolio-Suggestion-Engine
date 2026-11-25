// Quant-terminal topbar wired to live state.
//
// Differences from the mockup that I deliberately keep:
//   - The LED + clock reflect *real* connection and time. The mockup
//     hardcoded "NYSE OPEN · 14:23:08 ET"; here the LED greens only when
//     we have a live tick within the last 15 seconds, ambers when the
//     stream is reconnecting or markets are closed, reds on terminal
//     error. The clock ticks 1s in the user's local timezone (we show
//     it in NYSE time, which is the relevant trading clock).
//   - The "tape" surfaces the live valuation rows. With no portfolio
//     selected it shows the brand strapline so it never looks empty.

import { useEffect, useMemo, useState } from 'react';

import {
  useActivatePortfolio,
  useDeletePortfolio,
  useDuplicatePortfolio,
  usePortfolioList,
  useRenamePortfolio,
} from '../hooks/usePortfolios';
import type { StreamState, StreamStatus } from '../hooks/useStream';
import type { Route } from '../hooks/useHashRoute';
import type { PortfolioMeta, Valuation } from '../types';
import {
  formatPercent,
  formatMoney,
  isNyseOpen,
  formatQuoteAsOf,
  formatSyncAge,
} from '../utils/format';

interface Props {
  route: Route;
  onRouteChange: (next: Route) => void;
  stream: StreamState;
  activePortfolioId: string | null;
  valuation?: Valuation | null;
}

const ROUTES: Array<{ id: Route; label: string }> = [
  { id: 'setup', label: '01 · SETUP' },
  { id: 'dashboard', label: '02 · DASHBOARD' },
  { id: 'reasoning', label: '03 · REASONING' },
];

export function Topbar({
  route,
  onRouteChange,
  stream,
  activePortfolioId,
  valuation = null,
}: Props) {
  const [clock, setClock] = useState(new Date());

  useEffect(() => {
    const id = window.setInterval(() => setClock(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const portfolios = usePortfolioList();
  const activate = useActivatePortfolio();
  const rename = useRenamePortfolio();
  const duplicate = useDuplicatePortfolio();
  const remove = useDeletePortfolio();

  const ledClass = ledColorClass(stream.status, clock, stream.lastFrameAt);
  const marketLabel = isNyseOpen(clock) ? 'NYSE OPEN' : 'MARKET CLOSED';
  const nyClockLabel = clock.toLocaleTimeString('en-US', {
    timeZone: 'America/New_York',
    hour12: false,
  });

  return (
    <div className="topbar">
      <div className="brand">
        <div className={`brand-dot ${ledClass}`} />
        <div>
          <div className="brand-name">QUANTSCOPE</div>
          <div className="brand-sub">PORTFOLIO ENGINE v0.1</div>
        </div>
      </div>
      <div className="tabs">
        {ROUTES.map((r) => (
          <a
            key={r.id}
            href={`#${r.id}`}
            className={route === r.id ? 'active' : ''}
            onClick={(e) => {
              e.preventDefault();
              onRouteChange(r.id);
            }}
          >
            {r.label}
          </a>
        ))}
      </div>
      <Tape stream={stream} valuation={valuation} now={clock} />
      <PortfoliosMenu
        items={portfolios.data?.items ?? []}
        activeId={portfolios.data?.active_id ?? null}
        onActivate={(id) => activate.mutate(id)}
        onRename={(id, name) => rename.mutate({ id, name })}
        onDuplicate={(id) => duplicate.mutate({ id })}
        onDelete={(id) => remove.mutate(id)}
      />
      <div className="clock" data-status={stream.status}>
        <span className={`led ${ledClass}`} />
        <span>
          {marketLabel} · {nyClockLabel} ET
        </span>
      </div>
      {activePortfolioId && (
        <div className="topbar-portfolio">
          <span className="topbar-portfolio-label">ACTIVE</span>
          <span className="topbar-portfolio-id">{activePortfolioId.slice(0, 8)}</span>
        </div>
      )}
    </div>
  );
}

function ledColorClass(
  status: StreamStatus,
  now: Date,
  lastFrameAt: Date | null,
): string {
  if (status === 'error') return 'led-red';
  if (status === 'reconnecting' || status === 'connecting') return 'led-amber';
  if (status === 'live' && lastFrameAt) {
    const ageMs = now.getTime() - lastFrameAt.getTime();
    if (ageMs < 30_000) return 'led-green';
    return 'led-amber';
  }
  if (!isNyseOpen(now)) return 'led-amber';
  return 'led-gray';
}

function Tape({
  stream,
  valuation,
  now,
}: {
  stream: StreamState;
  valuation: Valuation | null;
  now: Date;
}) {
  const valuationRows = valuation?.rows ?? stream.valuation?.rows;
  const statusAsOf = valuation?.status?.as_of ?? stream.valuation?.status?.as_of;

  const items = useMemo(() => {
    if (!valuationRows) return [];
    return valuationRows
      .filter((row) => row.shares > 0 && row.current_price !== null)
      .slice(0, 12);
  }, [valuationRows]);

  if (items.length === 0) {
    return (
      <div className="tape tape-empty">
        <span>Pick or build a portfolio to start streaming live valuations.</span>
      </div>
    );
  }

  const showAsOf =
    items.length > 0 &&
    statusAsOf &&
    (stream.status === 'live' || stream.status === 'reconnecting' || stream.status === 'connecting');

  const streamFresh =
    stream.lastFrameAt &&
    (stream.status === 'live' || stream.status === 'reconnecting' || stream.status === 'connecting');

  const sessionClosed = !isNyseOpen(now);
  const tapeMetaTitle = sessionClosed
    ? 'When the exchange is closed, Yahoo Finance usually returns the last closing price, so tape prices do not tick like during the session. Sync time shows the app is still connected and refreshing quotes on a poll interval.'
    : 'Sync time shows the last WebSocket update (valuation or keep-alive). Quotes refresh on a short server poll interval.';

  return (
    <div className="tape">
      {showAsOf && (
        <span className="tape-asof" title={tapeMetaTitle}>
          As of {formatQuoteAsOf(statusAsOf)}
          {sessionClosed ? ' · session closed · last close' : ''}
          {streamFresh && stream.lastFrameAt
            ? ` · sync ${formatSyncAge(stream.lastFrameAt, now)}`
            : ''}
        </span>
      )}
      {items.map((row) => {
        const changeSign = (row.unrealized_pnl_pct ?? 0) >= 0 ? 'pos' : 'neg';
        return (
          <span key={row.ticker} className="tape-item">
            <span className="tape-ticker">{row.ticker}</span>
            <span className="tape-price">{formatMoney(row.current_price, 2)}</span>
            <span className={`tape-change ${changeSign}`}>
              {formatPercent(row.unrealized_pnl_pct, 2)}
            </span>
          </span>
        );
      })}
    </div>
  );
}

interface PortfoliosMenuProps {
  items: PortfolioMeta[];
  activeId: string | null;
  onActivate: (id: string) => void;
  onRename: (id: string, name: string) => void;
  onDuplicate: (id: string) => void;
  onDelete: (id: string) => void;
}

function PortfoliosMenu({
  items,
  activeId,
  onActivate,
  onRename,
  onDuplicate,
  onDelete,
}: PortfoliosMenuProps) {
  const [open, setOpen] = useState(false);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const active = items.find((i) => i.id === activeId);

  return (
    <div className="portfolios-menu">
      <button
        type="button"
        className="portfolios-trigger"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="portfolios-label">PORTFOLIO</span>
        <span className="portfolios-name">
          {active ? active.name : items.length === 0 ? 'none yet' : 'unsaved'}
        </span>
        <span className="portfolios-caret">▾</span>
      </button>
      {open && (
        <div className="portfolios-popover" onMouseLeave={() => setOpen(false)}>
          {items.length === 0 ? (
            <div className="portfolios-empty">
              No saved portfolios yet. Build one from the Setup tab.
            </div>
          ) : (
            items.map((item) => (
              <PortfolioRow
                key={item.id}
                item={item}
                isRenaming={renamingId === item.id}
                onActivate={() => {
                  onActivate(item.id);
                  setOpen(false);
                }}
                onStartRename={() => setRenamingId(item.id)}
                onCommitRename={(newName) => {
                  onRename(item.id, newName);
                  setRenamingId(null);
                }}
                onCancelRename={() => setRenamingId(null)}
                onDuplicate={() => onDuplicate(item.id)}
                onDelete={() => {
                  if (window.confirm(`Delete "${item.name}"? This cannot be undone.`)) {
                    onDelete(item.id);
                  }
                }}
              />
            ))
          )}
        </div>
      )}
    </div>
  );
}

interface PortfolioRowProps {
  item: PortfolioMeta;
  isRenaming: boolean;
  onActivate: () => void;
  onStartRename: () => void;
  onCommitRename: (next: string) => void;
  onCancelRename: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
}

function PortfolioRow({
  item,
  isRenaming,
  onActivate,
  onStartRename,
  onCommitRename,
  onCancelRename,
  onDuplicate,
  onDelete,
}: PortfolioRowProps) {
  const [draftName, setDraftName] = useState(item.name);

  return (
    <div className={'portfolios-row' + (item.active ? ' active' : '')}>
      {isRenaming ? (
        <input
          autoFocus
          value={draftName}
          onChange={(e) => setDraftName(e.target.value)}
          onBlur={() => onCommitRename(draftName.trim() || item.name)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') onCommitRename(draftName.trim() || item.name);
            if (e.key === 'Escape') onCancelRename();
          }}
          className="portfolios-rename-input"
        />
      ) : (
        <button type="button" className="portfolios-activate" onClick={onActivate}>
          <span className="portfolios-item-name">{item.name}</span>
          <span className="portfolios-item-id">{item.id.slice(0, 8)}</span>
        </button>
      )}
      <div className="portfolios-row-actions">
        <button
          type="button"
          title="Rename"
          onClick={(e) => {
            e.stopPropagation();
            setDraftName(item.name);
            onStartRename();
          }}
        >
          ✎
        </button>
        <button
          type="button"
          title="Duplicate"
          onClick={(e) => {
            e.stopPropagation();
            onDuplicate();
          }}
        >
          ⎘
        </button>
        <button
          type="button"
          title="Delete"
          className="danger"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
        >
          🗑
        </button>
      </div>
    </div>
  );
}
