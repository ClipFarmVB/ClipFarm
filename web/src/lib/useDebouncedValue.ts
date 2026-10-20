/**
 * `value`, held back until it has stopped changing for `delayMs` (CF-186).
 *
 * Written for the game page's clip filters. Its sliders change on every step of
 * a drag, and each change used to refetch `GET /games/{id}/clips`, which the api
 * rate limits per caller at 60 a minute. One drag could spend that whole budget
 * and replace the page with a 429. Fed this instead of the raw filters, the
 * fetch runs once per settled change.
 *
 * The first value comes back immediately, so an initial load is not delayed.
 */
import { useEffect, useState } from "react";

export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [settled, setSettled] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);

  return settled;
}
