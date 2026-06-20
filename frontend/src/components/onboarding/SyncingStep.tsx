import { useCallback, useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useToday } from "../../hooks";
import SetupLoader from "../loaders/SetupLoader";

const POLL_INTERVAL_MS = 2_000;
const MAX_WAIT_MS = 20_000;

export interface Props {
  onDone: () => void;
}

/**
 * Post-setup splash. The backend's kicked-off daily refresh is fetching
 * schedules, standings and rosters; every cached query is stale (the
 * followed teams just changed underneath them), so invalidate the lot,
 * then poll /today until games appear — or stop waiting after ~20s and
 * let the dashboard fill in live.
 */
export default function SyncingStep({ onDone }: Props) {
  const queryClient = useQueryClient();
  const today = useToday();
  const mountedAtRef = useRef<number>(Date.now());
  const finishedRef = useRef(false);
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;

  const finish = useCallback(() => {
    if (finishedRef.current) {
      return;
    }
    finishedRef.current = true;
    onDoneRef.current();
  }, []);

  useEffect(() => {
    void queryClient.invalidateQueries();
  }, [queryClient]);

  const refetchToday = today.refetch;
  useEffect(() => {
    const interval = window.setInterval(() => {
      void refetchToday();
    }, POLL_INTERVAL_MS);
    const timeout = window.setTimeout(finish, MAX_WAIT_MS);
    return () => {
      window.clearInterval(interval);
      window.clearTimeout(timeout);
    };
  }, [refetchToday, finish]);

  // Only data fetched AFTER this step mounted counts — a cached /today
  // from before the follow change must not cut the wait short.
  useEffect(() => {
    if (
      today.dataUpdatedAt >= mountedAtRef.current &&
      (today.data?.games.length ?? 0) > 0
    ) {
      finish();
    }
  }, [today.data, today.dataUpdatedAt, finish]);

  // The Prompt 3 build loader. No granular backend progress signal exists, so
  // run its looping demo (cycling status + indeterminate bar) while we poll.
  return (
    <div className="flex justify-center py-12">
      <SetupLoader />
    </div>
  );
}
