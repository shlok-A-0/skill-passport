/** Select value for "Paste a job description"; never sent to the server. */
export const JOB_DESCRIPTION_OPTION = "__job_description__";
export const JD_TITLE_MAX = 120;
export const JD_MIN = 50;
export const JD_MAX = 8000;

/** The target_role to submit: the extracted custom role id, or the selected fixed role. */
export function effectiveTargetRole(selected: string, extractedId: string | null): string {
  if (selected === JOB_DESCRIPTION_OPTION) return extractedId ?? "";
  return selected;
}

/** Why "Extract requirements" can't run yet, or null when the input is long enough. */
export function jobDescriptionInputError(title: string, description: string): string | null {
  const cleanTitle = title.trim();
  const cleanText = description.trim();
  if (!cleanTitle) return "Enter the job title.";
  if (cleanTitle.length > JD_TITLE_MAX) return `Keep the job title to ${JD_TITLE_MAX} characters or fewer.`;
  if (cleanText.length < JD_MIN) return `Paste at least ${JD_MIN} characters of the job description.`;
  if (cleanText.length > JD_MAX) return `Shorten the job description to ${JD_MAX.toLocaleString("en-US")} characters or fewer.`;
  return null;
}

/** "N phrases could not be mapped", or null when nothing was dropped. */
export function droppedSummary(count: number): string | null {
  if (count <= 0) return null;
  return `${count} phrase${count === 1 ? "" : "s"} could not be mapped`;
}
