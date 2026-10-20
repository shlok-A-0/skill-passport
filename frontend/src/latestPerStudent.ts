/** One passport per student: the most recently created one for each GitHub username. */
export function latestPerStudent<T extends { github_username: string; created_at: string }>(passports: T[]): T[] {
  const latest = new Map<string, T>();
  for (const passport of passports) {
    const key = passport.github_username.toLowerCase();
    const current = latest.get(key);
    if (!current || passport.created_at > current.created_at) latest.set(key, passport);
  }
  return [...latest.values()];
}
