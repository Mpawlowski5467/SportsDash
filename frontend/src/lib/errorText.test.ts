import { describe, expect, it } from "vitest";

import { errorText } from "./errorText";

describe("errorText", () => {
  it("reads the sentence out of an API error body", () => {
    expect(errorText(new Error('{"detail":"provider timed out"}'))).toBe(
      " — provider timed out",
    );
  });

  it("gives a plain message as it is", () => {
    expect(errorText(new Error("Failed to fetch"))).toBe(" — Failed to fetch");
  });

  it("gives JSON it cannot read a sentence from as it is", () => {
    expect(errorText(new Error('{"detail":[{"msg":"bad"}]}'))).toBe(
      ' — {"detail":[{"msg":"bad"}]}',
    );
    expect(errorText(new Error("404"))).toBe(" — 404");
  });

  it("says nothing when there is nothing to say", () => {
    expect(errorText(new Error(""))).toBe("");
    expect(errorText(undefined)).toBe("");
    expect(errorText("a string, not an error")).toBe("");
  });
});
