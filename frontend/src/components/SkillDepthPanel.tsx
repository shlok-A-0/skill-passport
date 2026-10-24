import { ExternalLink, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type SkillDepth } from "../api";

const DISPLAY = ["python", "react", "fastapi", "docker", "git", "unit-testing", "scikit-learn"];
const NAMES: Record<string, string> = {
  python: "Python", react: "React", fastapi: "FastAPI", docker: "Docker",
  git: "Git", "unit-testing": "Unit Testing", "scikit-learn": "scikit-learn",
};
const STATUS: Record<string, string> = {
  observed: "Observed",
  not_observed: "Not observed in analyzed files",
  not_applicable: "Not applicable",
  insufficient_evidence: "Insufficient evidence",
};

export default function SkillDepthPanel({ results }: {
  results: Record<string, SkillDepth | null> | undefined;
}) {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  useEffect(() => {
    if (results !== undefined) return;
    let active = true;
    api.features().then((features) => {
      if (active) setEnabled(Boolean(features.skill_depth));
    }).catch(() => undefined);
    return () => { active = false; };
  }, [results]);

  if (results === undefined) return enabled ? <p className="unavailable-note">Skill depth is not recorded for this passport. Re-check to analyse it.</p> : null;
  const profiles = DISPLAY.map((id) => [id, results[id]] as const).filter((item) => item[1]);
  if (!profiles.length) return null;
  return (
    <section className="panel skill-depth-panel">
      <div className="panel-heading">
        <div><h2>Skill depth (pilot)</h2></div>
        <Sparkles size={17} color="#c95118" />
      </div>
      <p className="skill-depth-disclaimer">Patterns found in the analysed sample, separate from evidence levels. Not a proficiency score. Missing patterns do not mean a skill is absent.</p>
      {(profiles[0]?.[1]?.coverage.notes ?? []).map((note) => <p className="muted" key={note}>{note}</p>)}
      {profiles.map(([id, result]) => result && (
        <details className="skill-depth-profile" key={id}>
          <summary>
            <strong>{NAMES[id]}</strong>
            <span>{result.score === null ? "Insufficient evidence" : String(result.score) + "/100"}</span>
            <small>{result.label} · {result.coverage.percent}% covered</small>
          </summary>
          {result.coverage.percent < 100 && <p className="muted">Some criteria could not be checked in the sample.</p>}
          <ul>
            {result.competencies.map((item) => (
              <li key={item.id}>
                <div><strong>{item.label}</strong><span className={"skill-depth-status status-" + item.status}>{STATUS[item.status]}</span></div>
                <p>{item.explanation}</p>
                {item.evidence.map((citation) => (
                  <a className="skill-depth-citation" key={citation.url + "-" + citation.start_line}
                    href={citation.url} target="_blank" rel="noreferrer">
                    {citation.repo}/{citation.file_path} · L{citation.start_line}–L{citation.end_line}
                    <ExternalLink size={11} />
                  </a>
                ))}
              </li>
            ))}
          </ul>
          <small className="muted">Rubric {result.rubric_version}. Scores compare evidence within the same skill only.</small>
        </details>
      ))}
    </section>
  );
}
