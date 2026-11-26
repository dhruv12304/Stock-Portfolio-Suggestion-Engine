// Setup screen — investment amount + strategy picker + blend slider + build.
//
// Differences from the mockup Setup component:
//   - The "Resume last build" button maps to the Portfolios dropdown in
//     the topbar; clicking Resume here simply activates the most-recent
//     saved portfolio and routes to the Dashboard.
//   - Validation footer reports the same explicit state ("ready / set
//     amount / pick strategy") that the user sees in the mockup.

import { useMemo, useState } from 'react';

import { useBuildPortfolio, usePortfolioList, useActivatePortfolio, useStrategies } from '../hooks/usePortfolios';

interface Props {
  onBuilt: () => void;
}

const PRESETS = [5_000, 10_000, 25_000, 50_000, 100_000];

const MIN_BUDGET = 5_000;

export function Setup({ onBuilt }: Props) {
  const strategies = useStrategies();
  const portfolios = usePortfolioList();
  const buildMutation = useBuildPortfolio();
  const activateMutation = useActivatePortfolio();

  const [amount, setAmount] = useState<number>(25_000);
  const [selected, setSelected] = useState<string[]>([]);
  const [blend, setBlend] = useState<number>(60);
  const [portfolioName, setPortfolioName] = useState<string>('');

  const valid = amount >= MIN_BUDGET && selected.length > 0;
  const weights = useMemo<number[] | undefined>(() => {
    if (selected.length !== 2) return undefined;
    return [blend / 100, 1 - blend / 100];
  }, [selected.length, blend]);

  const mostRecent = portfolios.data?.items
    .slice()
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0];

  function toggleStrategy(name: string) {
    setSelected((current) => {
      if (current.includes(name)) {
        return current.filter((n) => n !== name);
      }
      if (current.length >= 2) {
        // Replace the oldest pick so the slider always references [0, 1].
        return [current[1], name];
      }
      return [...current, name];
    });
  }

  function handleBuild() {
    if (!valid) return;
    buildMutation.mutate(
      {
        investment_amount: amount,
        strategies: selected,
        strategy_weights: weights,
        save: true,
        name: portfolioName || undefined,
      },
      {
        onSuccess: () => {
          setPortfolioName('');
          onBuilt();
        },
      },
    );
  }

  function handleResume() {
    if (!mostRecent) return;
    activateMutation.mutate(mostRecent.id, { onSuccess: onBuilt });
  }

  return (
    <div className="setup-wrap">
      <div className="setup-title">▸ NEW PORTFOLIO · STEP 01 / 01</div>
      <div className="setup-h1">
        Choose <span className="acc">capital and style</span> → scoring, limits, and allocation run
        automatically.
      </div>

      <Field
        label="▸ Investment Amount"
        hint={`min $${MIN_BUDGET.toLocaleString()} · whole-share execution, fractional shares disabled`}
      >
        <div className="amount-input">
          <span className="amount-prefix">$</span>
          <input
            type="text"
            value={amount.toLocaleString()}
            onChange={(e) => {
              const next = parseInt(e.target.value.replace(/[^\d]/g, ''), 10) || 0;
              setAmount(next);
            }}
            inputMode="numeric"
            aria-label="Investment amount"
          />
          <div className="amount-quick">
            {PRESETS.map((value) => (
              <button
                key={value}
                type="button"
                onClick={() => setAmount(value)}
                className={amount === value ? 'active' : ''}
              >
                {value >= 1000 ? `${value / 1000}K` : value}
              </button>
            ))}
          </div>
        </div>
      </Field>

      <Field
        label="▸ Strategy Selection"
        hint={`${selected.length} of 2 selected`}
      >
        {strategies.isLoading && <div className="muted">Loading strategies…</div>}
        {strategies.error && (
          <div className="error">Could not load strategies: {(strategies.error as Error).message}</div>
        )}
        <div className="strats">
          {strategies.data?.map((row, i) => {
            const isSelected = selected.includes(row.name);
            return (
              <button
                key={row.name}
                type="button"
                className={'strat' + (isSelected ? ' selected' : '')}
                onClick={() => toggleStrategy(row.name)}
                aria-pressed={isSelected}
              >
                <span className="strat-num">[{String(i + 1).padStart(2, '0')}]</span>
                <span className="strat-name">{row.name}</span>
                <span className="strat-blurb">{row.description}</span>
                <span className="strat-ck">{isSelected ? '✓ ACTIVE' : '○ select'}</span>
              </button>
            );
          })}
        </div>
      </Field>

      {selected.length === 2 && (
        <Field label="▸ Blend Weight">
          <div className="blend">
            <div className="blend-legend">
              <span>
                <span className="sw sw-accent" /> {selected[0]} ·{' '}
                <span className="num">{blend}%</span>
              </span>
              <span>
                <span className="sw sw-purple" /> {selected[1]} ·{' '}
                <span className="num">{100 - blend}%</span>
              </span>
            </div>
            <input
              type="range"
              min={10}
              max={90}
              step={5}
              value={blend}
              onChange={(e) => setBlend(parseInt(e.target.value, 10))}
              className="blend-slider"
              aria-label={`${selected[0]} weight`}
            />
          </div>
        </Field>
      )}

      <Field label="▸ Portfolio name (optional)">
        <input
          type="text"
          className="text-input"
          placeholder="e.g. Growth Core 25K"
          value={portfolioName}
          onChange={(e) => setPortfolioName(e.target.value)}
          maxLength={80}
        />
      </Field>

      <div className="setup-constraints">
        <span>
          ETF FLOOR <strong>≥ 30%</strong>
        </span>
        <span>
          SECTOR CAP <strong>≤ 40%</strong>
        </span>
        <span>
          WHOLE-SHARE <strong>floor(div)</strong>
        </span>
      </div>

      <div className="setup-cta">
        <button
          type="button"
          className="btn btn-primary"
          onClick={handleBuild}
          disabled={!valid || buildMutation.isPending}
        >
          {buildMutation.isPending ? '… building' : '▸ BUILD PORTFOLIO'}
        </button>
        {mostRecent && (
          <button
            type="button"
            className="btn"
            onClick={handleResume}
            disabled={activateMutation.isPending}
          >
            ▾ RESUME LAST ({mostRecent.name})
          </button>
        )}
        <div className="setup-cta-spacer" />
        <div className="setup-cta-status">
          {!valid && amount < MIN_BUDGET && '✗ MIN $5,000 REQUIRED'}
          {!valid && amount >= MIN_BUDGET && selected.length === 0 && '✗ PICK ≥1 STRATEGY'}
          {valid && '✓ VALID · READY TO BUILD'}
        </div>
      </div>

      {buildMutation.error && (
        <div className="error">Build failed: {(buildMutation.error as Error).message}</div>
      )}
    </div>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="field">
      <div className="field-label">
        {label}
        {hint && <span className="field-hint"> {hint}</span>}
      </div>
      {children}
    </div>
  );
}
