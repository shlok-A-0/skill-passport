export function nextJobPollDelay(current: number): number {
  return Math.min(current * 2, 15_000);
}

export function consecutivePollFailuresAfterRequest(
  failures: number,
  allRequiredRequestsSucceeded: boolean,
): number {
  return allRequiredRequestsSucceeded ? 0 : failures + 1;
}
