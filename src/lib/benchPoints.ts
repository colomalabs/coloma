import type { BenchPoint } from "../types";

// series_id names the grid coordinate ("4096 tokens"): the measured median_prompt_tokens wobbles by
// a few tokens between batches of the same series (random ids re-tokenize differently), so charts
// key rows by the series and position them at the length it names.
export function promptTokensOf(point: BenchPoint): number {
  const parsed = Number.parseInt(point.series_id, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : point.median_prompt_tokens;
}
