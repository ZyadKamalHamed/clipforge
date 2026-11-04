// How long a job has left, from its progress samples: [seconds, share done from 0 to 1].

const SETTLE_S = 15; // under this much progress to go on, a guess jumps about too much to show

export function etaSeconds(samples: Array<[number, number]>, now: number): number | null {
  if (samples.length < 2) return null;
  const [t0, p0] = samples[0];
  const [t1, p1] = samples[samples.length - 1];
  if (now - t0 < SETTLE_S || t1 <= t0) return null;
  const rate = (p1 - p0) / (t1 - t0);
  if (!(rate > 0)) return null; // stalled, or going backwards
  const left = Math.max(0, 1 - p1) / rate - (now - t1);
  return Math.max(0, Math.round(left));
}
