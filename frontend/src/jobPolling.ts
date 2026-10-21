export const MAX_CONSECUTIVE_POLL_FAILURES = 8;

export function nextJobPollDelay(current: number): number {
  return Math.min(current * 2, 15_000);
}

export type JobPollAttempt<TJob, TPassport> =
  | { ok: true; job: TJob; passport?: TPassport; consecutiveFailures: number }
  | { ok: false; error: unknown; consecutiveFailures: number };

/** Fetch status and, for completed jobs, load the result before resetting failures. */
export async function pollJobAttempt<TJob extends { status: string }, TPassport>(
  previousFailures: number,
  getJob: () => Promise<TJob>,
  loadPassport: () => Promise<TPassport>,
): Promise<JobPollAttempt<TJob, TPassport>> {
  let consecutiveFailures = previousFailures;
  try {
    const job = await getJob();
    if (job.status === "done") {
      const passport = await loadPassport();
      consecutiveFailures = 0;
      return { ok: true, job, passport, consecutiveFailures };
    }
    consecutiveFailures = 0;
    return { ok: true, job, consecutiveFailures };
  } catch (error) {
    return { ok: false, error, consecutiveFailures: consecutiveFailures + 1 };
  }
}

export function shouldRememberGenerationJob(
  jobId: string,
  pendingRecheck: string | null,
  jobType: string | null = null,
): boolean {
  if (jobType === "recheck") return false;
  if (jobType === "generation") return true;
  if (!pendingRecheck) return true;
  try {
    return (JSON.parse(pendingRecheck) as { job_id?: string }).job_id !== jobId;
  } catch {
    return true;
  }
}
