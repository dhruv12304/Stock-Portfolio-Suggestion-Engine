// User-facing copy for machine codes returned by the risk API.

export function formatRiskNote(note: string | null): string | null {
  if (!note) return null;
  switch (note) {
    case 'insufficient_history':
      return 'Not enough daily samples in this Equity History range for the scatter/correlation panels. Try a longer trading-day window such as 3M. Sharpe remains available from its own 3M / 6M / 1Y / 2Y buttons.';
    case 'no_funded_positions':
      return 'There are no funded positions in this snapshot to analyze.';
    default:
      if (/^[a-z][a-z0-9_]*$/.test(note)) {
        return note
          .split('_')
          .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
          .join(' ');
      }
      return note;
  }
}

export function riskScatterEmptyCopy(note: string | null | undefined): string {
  if (note === 'insufficient_history') {
    return 'There are not enough daily prices in this Equity History range to draw annualized risk/return. Select a longer trading-day window such as 3M. Sharpe still uses its own independent window selector.';
  }
  return 'Risk and return need enough overlapping daily returns in the selected range. Ensure holdings are funded or choose a longer window.';
}
