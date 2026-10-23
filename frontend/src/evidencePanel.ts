import type { Evidence } from "./api";

export function showsNotCountedNote(item: Evidence): boolean {
  return (item.type === "dependency" || item.type === "config") && "authored" in item.checks && !item.checks.authored;
}
