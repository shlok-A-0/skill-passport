import { ArrowUpRight, RefreshCw } from "lucide-react";
import type { Passport } from "../api";
import { skillName } from "../skillNames";

export default function GrowthCard({
  task,
  onRecheck,
  busy,
}: {
  task: Passport["growth_tasks"][number];
  onRecheck: () => void;
  busy: boolean;
}) {
  return (
    <article className="growth-card">
      <div className="growth-card-top">
        <span className="growth-skill">{skillName(task.skill_id)}</span>
        <span className="repo-tag">{task.target_repo}</span>
      </div>
      <p>{task.task}</p>
      <div className="proof-target"><ArrowUpRight size={14} /> Proof this creates: {task.evidence_it_will_create}</div>
      <button className="button button-light" onClick={onRecheck} disabled={busy}>
        <RefreshCw size={14} className={busy ? "spin" : ""} /> {busy ? "Re-checking…" : "Done · re-check"}
      </button>
    </article>
  );
}
