/**
 * Centralized time formatting utilities.
 * All timestamps display in local time; storage remains UTC.
 */

const STORAGE_KEY = 'trustbook_tz';

/**
 * Get user's preferred timezone (from localStorage or browser default).
 */
export function getTimezone(): string {
  if (typeof window !== 'undefined') {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) return stored;
  }
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

/**
 * Set user's preferred timezone.
 */
export function setTimezone(tz: string): void {
  if (typeof window !== 'undefined') {
    localStorage.setItem(STORAGE_KEY, tz);
  }
}

/**
 * Get timezone abbreviation (e.g., "PST", "UTC").
 */
export function getTimezoneAbbr(): string {
  const tz = getTimezone();
  const formatter = new Intl.DateTimeFormat('zh-CN', {
    timeZone: tz,
    timeZoneName: 'short',
  });
  const parts = formatter.formatToParts(new Date());
  const tzPart = parts.find(p => p.type === 'timeZoneName');
  return tzPart?.value || tz;
}

/**
 * Parse ISO timestamp, treating naive timestamps as UTC.
 */
function parseAsUTC(iso: string): Date {
  // If no timezone info, assume UTC
  if (!iso.endsWith('Z') && !iso.includes('+') && !/\d{2}:\d{2}$/.test(iso.slice(-6))) {
    return new Date(iso + 'Z');
  }
  return new Date(iso);
}

function pad2(n: string): string {
  return n.length === 1 ? `0${n}` : n;
}

function formatParts(date: Date, tz: string, withTime: boolean, withSeconds: boolean): string {
  const formatter = new Intl.DateTimeFormat('zh-CN', {
    timeZone: tz,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    ...(withTime
      ? {
          hour: '2-digit',
          minute: '2-digit',
          ...(withSeconds ? { second: '2-digit' } : {}),
          hour12: false,
        }
      : {}),
  });

  const parts = formatter.formatToParts(date);
  const get = (type: Intl.DateTimeFormatPartTypes) => parts.find(p => p.type === type)?.value;

  const y = get('year') || '';
  const m = pad2(get('month') || '');
  const d = pad2(get('day') || '');
  if (!withTime) return `${y}-${m}-${d}`;

  const hh = pad2(get('hour') || '00');
  const mm = pad2(get('minute') || '00');
  const ss = withSeconds ? pad2(get('second') || '00') : null;
  return ss ? `${y}-${m}-${d} ${hh}:${mm}:${ss}` : `${y}-${m}-${d} ${hh}:${mm}`;
}

/**
 * Format ISO timestamp to local date string.
 * Example: "2026-02-02"
 */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    const date = parseAsUTC(iso);
    return formatParts(date, getTimezone(), false, false);
  } catch {
    return '—';
  }
}

/**
 * Format ISO timestamp to local date + time string.
 * Example: "2026-02-02 10:30"
 */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    const date = parseAsUTC(iso);
    return formatParts(date, getTimezone(), true, false);
  } catch {
    return '—';
  }
}

/**
 * Format ISO timestamp to local date + time string (with seconds).
 * Example: "2026-02-02 10:30:18"
 */
export function formatDateTimeSeconds(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    const date = parseAsUTC(iso);
    return formatParts(date, getTimezone(), true, true);
  } catch {
    return '—';
  }
}

/**
 * Format ISO timestamp to relative time (e.g., "2 hours ago").
 * Falls back to formatDateTime for older dates.
 */
export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    const date = parseAsUTC(iso);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffSec = Math.floor(diffMs / 1000);
    const diffMin = Math.floor(diffSec / 60);
    const diffHour = Math.floor(diffMin / 60);
    const diffDay = Math.floor(diffHour / 24);

    if (diffSec < 60) return '刚刚';
    if (diffMin < 60) return `${diffMin} 分钟前`;
    if (diffHour < 24) return `${diffHour} 小时前`;
    if (diffDay < 7) return `${diffDay} 天前`;
    
    return formatDateTime(iso);
  } catch {
    return '—';
  }
}

/**
 * Format ISO timestamp to short time only.
 * Example: "10:30"
 */
export function formatTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    const date = parseAsUTC(iso);
    return formatParts(date, getTimezone(), true, false).split(' ')[1] || '—';
  } catch {
    return '—';
  }
}
