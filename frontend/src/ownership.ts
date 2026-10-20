/** Compare GitHub usernames without treating case or surrounding whitespace as a change. */
export function sameGitHubUsername(left: string, right: string): boolean {
  return left.trim().toLowerCase() === right.trim().toLowerCase();
}
