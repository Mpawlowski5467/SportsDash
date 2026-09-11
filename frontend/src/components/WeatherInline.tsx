import type { Weather } from "../types";

/**
 * Compact venue-weather block: a vector icon for the WMO code, the current
 * temperature + condition, and a sub-line with high/low, wind, and precip
 * chance. Units come from the payload (`metric` → °C/km·h, `imperial` →
 * °F/mph). Shared by the map stadium panel and the pre-game detail modal.
 */

/** Map a WMO weather code to a representative emoji. */
export function wmoEmoji(code: number): string {
  if (code === 0) return "☀️";
  if (code === 1) return "🌤️";
  if (code === 2) return "⛅";
  if (code === 3) return "☁️";
  if (code === 45 || code === 48) return "🌫️";
  if (code >= 51 && code <= 57) return "🌦️";
  if (code >= 61 && code <= 67) return "🌧️";
  if (code >= 71 && code <= 77) return "🌨️";
  if (code >= 80 && code <= 82) return "🌧️";
  if (code === 85 || code === 86) return "🌨️";
  if (code >= 95) return "⛈️";
  return "🌡️";
}

/** Weather artwork for the stadium panel, with no platform-dependent emoji. */
export function WeatherIcon({ code }: { code: number }) {
  const sun = code >= 0 && code <= 2;
  const cloud = code >= 1 && code !== 45 && code !== 48;
  const rain = (code >= 51 && code <= 67) || (code >= 80 && code <= 82) || code >= 95;
  const snow = (code >= 71 && code <= 77) || code === 85 || code === 86;
  const fog = code === 45 || code === 48;
  return (
    <svg viewBox="0 0 32 32" width="30" height="30" fill="none" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="shrink-0">
      {sun && <g stroke="#fbbf24"><circle cx="12" cy="12" r="5" fill="#fbbf24" fillOpacity=".15"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/></g>}
      {cloud && <path d="M9 23a5 5 0 0 1-1-10 7 7 0 0 1 13-2 6 6 0 1 1 3 12Z" stroke="currentColor" fill="currentColor" fillOpacity=".1"/>}
      {rain && <path d="m10 26-1 3m8-3-1 3m8-3-1 3" stroke="#60a5fa"/>}
      {snow && <g fill="#bae6fd"><circle cx="10" cy="28" r="1.3"/><circle cx="17" cy="27" r="1.3"/><circle cx="24" cy="28" r="1.3"/></g>}
      {fog && <path d="M5 10h22M3 16h21M8 22h21" stroke="currentColor"/>}
      {code >= 95 && <path d="m18 16-4 7h4l-3 6" stroke="#fbbf24"/>}
      {code < 0 && <path d="M13 6a3 3 0 0 1 6 0v14a6 6 0 1 1-6 0ZM16 11v13" stroke="currentColor"/>}
    </svg>
  );
}

export default function WeatherInline({ weather }: { weather: Weather }) {
  const tempUnit = weather.units === "imperial" ? "°F" : "°C";
  const windUnit = weather.units === "imperial" ? "mph" : "km/h";
  const round = (n: number) => Math.round(n);

  const hasRange = weather.high !== null || weather.low !== null;

  return (
    <div className="flex items-center gap-3">
      <WeatherIcon code={weather.code} />
      <div className="min-w-0">
        <div className="flex items-baseline gap-2">
          <span className="text-lg font-semibold text-zinc-100 tabular-nums">
            {round(weather.temperature)}
            {tempUnit}
          </span>
          <span className="truncate text-sm text-zinc-300">
            {weather.condition}
          </span>
        </div>
        <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-zinc-500">
          {hasRange && (
            <span className="tabular-nums">
              H {weather.high !== null ? round(weather.high) : "—"}° · L{" "}
              {weather.low !== null ? round(weather.low) : "—"}°
            </span>
          )}
          <span className="tabular-nums">
            Wind {round(weather.wind_speed)} {windUnit}
          </span>
          {weather.precip_chance !== null && (
            <span className="tabular-nums">
              Precip {weather.precip_chance}%
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
