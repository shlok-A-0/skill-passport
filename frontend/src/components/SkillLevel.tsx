import type { PassportSkill } from "../api";
import { adjustmentLabel } from "../interview";
import LadderPill from "./LadderPill";

/** A skill's level, tagged with how a recorded interview outcome moved it (recruiter data only). */
export default function SkillLevel({ skill, level }: { skill: PassportSkill; level?: PassportSkill["level"] }) {
  const interview = skill.interview;
  return (
    <span className="skill-level">
      <LadderPill level={level ?? skill.level} />
      {interview && (
        <span className={`interview-tag interview-${interview.outcome}`}
          title={`${interview.outcome_label} in the interview (${interview.questions} question${interview.questions === 1 ? "" : "s"}, ${interview.assessor_id}). Level from the code checks: ${interview.evidence_level}.`}>
          {adjustmentLabel(interview)}
        </span>
      )}
    </span>
  );
}
