export function nextJobPollDelay(current: number): number {
  return Math.min(current * 2, 15_000);
}
