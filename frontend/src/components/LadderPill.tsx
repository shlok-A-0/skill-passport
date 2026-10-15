import type { PassportSkill } from "../api";

const labels: Record<PassportSkill["level"], string> = {
  Claimed: "Unverified",
  Used: "Used",
  Applied: "Applied",
  Demonstrated: "Demonstrated",
};

export default function LadderPill({ level }: { level: PassportSkill["level"] }) {
  return <span className={`ladder-pill level-${level.toLowerCase()}`}>{labels[level]}</span>;
}
