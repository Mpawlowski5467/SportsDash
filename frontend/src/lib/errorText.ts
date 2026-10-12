/**
 * What a failed request said, ready to follow a sentence: " — provider
 * timed out", or "" when it said nothing. The API answers a failure with
 * `{"detail": "…"}`, and `ApiError` carries that body whole as its message:
 * this gives the sentence, not the JSON around it. Any other error gives
 * its message as it is.
 */
export function errorText(error: unknown): string {
  if (!(error instanceof Error) || !error.message) return "";
  try {
    const body: unknown = JSON.parse(error.message);
    if (
      typeof body === "object" &&
      body !== null &&
      "detail" in body &&
      typeof body.detail === "string"
    ) {
      return ` — ${body.detail}`;
    }
  } catch {
    // Not JSON: the message is already plain text.
  }
  return ` — ${error.message}`;
}
