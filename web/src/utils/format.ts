// Number formatting helpers shared across components.

export function formatMoney(value: number | null | undefined, decimals = 2): string {
  if (value === null || value === undefined) return '—';
  if (!Number.isFinite(value)) return '—';
  return value.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

export function formatPercent(value: number | null | undefined, decimals = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return '—';
  }
  const formatted = (value * 100).toFixed(decimals);
  const sign = value > 0 ? '+' : value < 0 ? '' : '';
  return `${sign}${formatted}%`;
}

export function formatSigned(value: number, decimals = 2): string {
  if (!Number.isFinite(value)) return '—';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })}`;
}

export function formatClock(date: Date): string {
  return date.toLocaleTimeString('en-US', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

/** Compact NY timestamp for quote snapshot lines (tape, banners). */
export function formatQuoteAsOf(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    const ny = d.toLocaleString('en-US', {
      timeZone: 'America/New_York',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    });
    return `${ny} ET`;
  } catch {
    return iso;
  }
}

/** How long ago the stream last received a frame (valuation or keep-alive ping). */
export function formatSyncAge(when: Date, now: Date): string {
  const sec = Math.floor((now.getTime() - when.getTime()) / 1000);
  if (!Number.isFinite(sec) || sec < 0) return 'just now';
  if (sec < 8) return 'just now';
  if (sec < 60) return `${sec}s ago`;
  const m = Math.floor(sec / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  return `${h}h ago`;
}

export function isNyseOpen(date: Date): boolean {
  // Best-effort: regular session 09:30-16:00 New York time, weekdays only.
  // (Does not honour US market holidays — fine for a project demo; the
  // amber/red LED states will reflect actual data freshness anyway.)
  const ny = new Date(date.toLocaleString('en-US', { timeZone: 'America/New_York' }));
  const day = ny.getDay();
  if (day === 0 || day === 6) return false;
  const minutes = ny.getHours() * 60 + ny.getMinutes();
  return minutes >= 9 * 60 + 30 && minutes < 16 * 60;
}
