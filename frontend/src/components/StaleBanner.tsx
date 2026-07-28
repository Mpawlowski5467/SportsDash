/**
 * Slim "the data on screen is stale" strip with a manual retry.
 *
 * A failed background refetch must NOT blank content that already loaded — on
 * an unattended kiosk one bad poll would otherwise leave an error message up
 * until the next successful one. Views guard their full-page error with
 * `isError && !query.data` and render this banner above the cached content
 * instead.
 */
export default function StaleBanner({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border border-amber-900/50 bg-amber-950/20 px-3 py-1.5">
      <p className="text-xs text-amber-400">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="shrink-0 text-xs font-medium text-amber-300 underline-offset-2 hover:underline"
      >
        Retry
      </button>
    </div>
  );
}
