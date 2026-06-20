/**
 * Error-message helper for the onboarding wizard. ApiError bodies are raw
 * response text — usually FastAPI's `{"detail": "..."}` — so try to pull
 * the human-readable detail out before falling back to the raw string.
 */

import { ApiError } from "../../api";

export function apiErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    try {
      const parsed: unknown = JSON.parse(err.message);
      if (
        parsed !== null &&
        typeof parsed === "object" &&
        "detail" in parsed &&
        typeof (parsed as { detail: unknown }).detail === "string"
      ) {
        return (parsed as { detail: string }).detail;
      }
    } catch {
      // Body was not JSON — fall through to the raw message.
    }
    return err.message;
  }
  if (err instanceof Error && err.message) {
    return err.message;
  }
  return "Request failed";
}
