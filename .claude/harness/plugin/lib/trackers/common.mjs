// Shared by every tracker adapter.
export class TrackerError extends Error {}

// `ticket line: Closes #NUM` -> `Closes #12`; `Ticket: SK-NUM` -> `Ticket: SK-3812`.
export function fillTicketLine(template, number) {
  if (!template.includes('NUM')) throw new TrackerError(`profile ticket line "${template}" has no NUM placeholder`);
  return template.replace(/NUM/g, String(number));
}
