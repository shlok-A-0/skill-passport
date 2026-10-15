import type { PassportSkill } from "../api";
import { LEVEL_HELP, LEVEL_LABELS } from "../levels";

export default function LadderPill({ level }: { level: PassportSkill["level"] }) {
  return <span className={`ladder-pill level-${level.toLowerCase()}`} title={LEVEL_HELP[level]}>{LEVEL_LABELS[level]}</span>;
}
