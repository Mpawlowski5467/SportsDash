import type { Weather } from "../types";

/**
 * Compact venue-weather block: an emoji for the WMO code, the current
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

export default function WeatherInline({ weather }: { weather: Weather }) {
  const tempUnit = weather.units === "imperial" ? "°F" : "°C";
  const windUnit = weather.units === "imperial" ? "mph" : "km/h";
  const round = (n: number) => Math.round(n);

  const hasRange = weather.high !== null || weather.low !== null;

  return (
    <div className="flex items-center gap-3">
      <span className="text-2xl leading-none" aria-hidden="true">
        {wmoEmoji(weather.code)}
      </span>
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
