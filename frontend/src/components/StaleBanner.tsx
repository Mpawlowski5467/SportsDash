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
    <div
      role="status"
      className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t-[3px] border-ink bg-tint px-4 py-2"
    >
      <p className="m-0 font-medium text-ink">{message}</p>
      <button type="button" onClick={onRetry} className="bs-btn bs-btn--ghost">
        Retry
      </button>
    </div>
  );
}
