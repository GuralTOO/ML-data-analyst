import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

export const cn = (...inputs) => twMerge(clsx(inputs));

// Compact relative-time string, like Slack DMs / ChatGPT sidebar:
//   "now" | "5m" | "3h" | "2d" | "3w" | "May 12"
// Input can be a Date, a number (ms), or an ISO/space-separated string. The
// backend returns SQLite TEXT in 'YYYY-MM-DD HH:MM:SS' form which is UTC.
export function formatRelativeTime(value) {
  if (value == null) return '';
  let d;
  if (value instanceof Date) {
    d = value;
  } else if (typeof value === 'number') {
    d = new Date(value);
  } else if (typeof value === 'string') {
    // SQLite gives "YYYY-MM-DD HH:MM:SS" without timezone — treat as UTC.
    const normalized = value.includes('T') ? value : value.replace(' ', 'T') + 'Z';
    d = new Date(normalized);
  } else {
    return '';
  }
  if (Number.isNaN(d.getTime())) return '';

  const diffMs = Date.now() - d.getTime();
  const m = Math.round(diffMs / 60_000);
  if (m < 1) return 'now';
  if (m < 60) return `${m}m`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h`;
  const days = Math.round(h / 24);
  if (days < 7) return `${days}d`;
  if (days < 28) return `${Math.round(days / 7)}w`;
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}
