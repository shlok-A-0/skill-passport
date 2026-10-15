import { API_BASE } from "./api";

const names = new Map<string, string>();

/** Load display names ("FastAPI", "PostgreSQL") once at startup; ids are the fallback. */
export async function loadSkillNames(): Promise<void> {
  try {
    const response = await fetch(`${API_BASE}/skills`);
    if (!response.ok) return;
    for (const skill of (await response.json()) as { id: string; name: string }[]) {
      names.set(skill.id, skill.name);
    }
  } catch {
    // Backend unreachable: pages fall back to readable ids and show their own errors.
  }
}

export function skillName(id: string): string {
  return names.get(id) ?? id.replace(/-/g, " ");
}
