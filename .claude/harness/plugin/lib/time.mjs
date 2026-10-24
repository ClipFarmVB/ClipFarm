// Time, without `date`. Git Bash on Windows has no timezone data and treats
// every zone as UTC without an error; an offset-form timestamp sorts wrong
// against GitHub's Z-suffixed ones. Everything here is Z-suffixed UTC.

export const nowUtc = (d = new Date()) => d.toISOString().replace(/\.\d{3}Z$/, 'Z');

// A stamp safe in Windows paths: no colons.
export const stamp = (d = new Date()) => nowUtc(d).replace(/[-:]/g, '');

export class TimeError extends Error {}

// Wall-clock time in an IANA zone -> UTC. Rejects abbreviations (EST is
// accepted by Intl but ignores daylight saving) and impossible dates.
export function toUtc(day, time, zone) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day ?? '') || !/^\d{2}:\d{2}$/.test(time ?? '') || !zone) {
    throw new TimeError('usage: utc YYYY-MM-DD HH:MM Area/City');
  }
  if (zone !== 'UTC' && !/^[A-Za-z_]+\/[A-Za-z0-9_+\-/]+$/.test(zone)) {
    throw new TimeError(`use an IANA zone like America/Toronto, not: ${zone}`);
  }
  let fmt;
  try {
    fmt = new Intl.DateTimeFormat('en-US', {
      timeZone: zone, hourCycle: 'h23', year: 'numeric', month: '2-digit',
      day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  } catch {
    throw new TimeError(`unknown timezone: ${zone}`);
  }
  const offset = (ms) => {
    const p = Object.fromEntries(fmt.formatToParts(ms).map((x) => [x.type, x.value]));
    return Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second) - ms;
  };
  const wall = Date.parse(`${day}T${time}:00Z`);
  if (Number.isNaN(wall) || new Date(wall).toISOString().slice(0, 16) !== `${day}T${time}`) {
    throw new TimeError(`invalid date: ${day} ${time}`);
  }
  // Two passes settle the offset across a DST boundary.
  let utc = wall - offset(wall);
  utc = wall - offset(utc);
  return nowUtc(new Date(utc));
}

// The run start: the last `run start: <UTC>` line of the log. A literal
// "$(date ...)" or an offset form would make every per-run count all-time.
export function runStartFrom(logText) {
  const lines = (logText ?? '').replace(/\r/g, '').split('\n').filter((l) => l.startsWith('run start: '));
  const v = lines.length ? lines[lines.length - 1].slice('run start: '.length).trim().split(/\s/)[0] : '';
  if (!v) return '';
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(v)) throw new TimeError(`run start "${v}" is not Z-suffixed UTC (YYYY-MM-DDTHH:MM:SSZ)`);
  return v;
}
