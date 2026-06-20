/**
 * Local-time formatting helpers.
 *
 * All inputs are ISO 8601 UTC strings from the API; everything here
 * renders in the viewer's local timezone. Formatters are built once at
 * module level (Intl.DateTimeFormat construction is expensive).
 */

const TIME_FORMAT = new Intl.DateTimeFormat("en-US", {
  hour: "numeric",
  minute: "2-digit",
  hour12: true,
});

const SHORT_DATE_FORMAT = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
});

/**
 * Newer ICU versions emit a narrow no-break space (U+202F) before the
 * AM/PM marker; normalize to a plain space so output is exactly "7:30 PM".
 */
function normalizeSpaces(value: string): string {
  return value.replace(/[\u202F\u00A0]/g, " ");
}

/** "7:30 PM" in the local timezone. */
export function formatTime(iso: string): string {
  return normalizeSpaces(TIME_FORMAT.format(new Date(iso)));
}

/** "Jun 11" in the local timezone. */
export function formatShortDate(iso: string): string {
  return normalizeSpaces(SHORT_DATE_FORMAT.format(new Date(iso)));
}

/** "Jun 11, 7:30 PM" in the local timezone. */
export function formatDateTime(iso: string): string {
  return `${formatShortDate(iso)}, ${formatTime(iso)}`;
}

/**
 * Local calendar-day key, "YYYY-MM-DD".
 *
 * Built from the local getFullYear/getMonth/getDate accessors — never
 * from toISOString(), which would shift the day across the UTC boundary.
 */
export function localDateKey(iso: string): string {
  const d = new Date(iso);
  const year = String(d.getFullYear()).padStart(4, "0");
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/**
 * Compact relative timestamp for feeds: "just now", "5m ago", "3h ago",
 * "2d ago"; anything older than 7 days falls back to formatShortDate.
 */
export function relativeTime(iso: string): string {
  const elapsedMs = Math.max(0, Date.now() - new Date(iso).getTime());
  const minutes = Math.floor(elapsedMs / 60_000);
  if (minutes < 1) {
    return "just now";
  }
  if (minutes < 60) {
    return `${minutes}m ago`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours}h ago`;
  }
  const days = Math.floor(hours / 24);
  if (days <= 7) {
    return `${days}d ago`;
  }
  return formatShortDate(iso);
}
