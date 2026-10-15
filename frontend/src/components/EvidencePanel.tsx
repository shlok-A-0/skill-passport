import { AlertTriangle, Check, CircleHelp, ExternalLink, FileCode2 } from "lucide-react";
import type { PassportSkill } from "../api";
import LadderPill from "./LadderPill";
import { skillName } from "../skillNames";

const checkLabels: Record<string, string> = {
  file_exists: "File exists in repository",
  lines_valid: "Cited lines are valid",
  authored: "Authored by you",
  not_fork: "Repository is not a fork",
  signature_found: "Skill signature found in code",
};

export default function EvidencePanel({ skill }: { skill: PassportSkill }) {
  return (
    <section className="panel evidence-panel">
      <div className="panel-heading">
        <div>
          <div className="eyebrow">EVIDENCE TRAIL</div>
          <h2>{skillName(skill.skill_id)}</h2>
        </div>
        <LadderPill level={skill.level} />
      </div>
      {skill.certified && <span className="certified-tag">Certificate noted · does not raise level</span>}
      {skill.evidence.length === 0 ? (
        <div className="empty-evidence">No authored code evidence was verified for this claim.</div>
      ) : (
        skill.evidence.map((item, index) => (
          <article className="evidence-item" key={`${item.url}-${index}`}>
            <div className="evidence-meta">
              <span className="file-icon"><FileCode2 size={16} /></span>
              <div>
                <strong>{item.repo}</strong>
                <div className="muted">{item.type === "commit" ? `Commit history · ${item.commit_sha.slice(0, 7)}` : `${item.file_path} · lines ${item.start_line}–${item.end_line}`}</div>
              </div>
              <a className="icon-link" href={item.url} target="_blank" rel="noreferrer" aria-label="Open exact GitHub lines">
                {item.type === "commit" ? "Open commit" : "Open on GitHub"} <ExternalLink size={14} />
              </a>
            </div>
            {item.snippet && (
              <pre className="code-snippet">{item.snippet.split("\n").map((line, lineIndex) => {
                const lineNumber = (item.snippet_start_line ?? item.start_line) + lineIndex;
                return (
                  <span className={`code-line ${lineNumber === item.matched_line ? "code-line-matched" : ""}`} key={lineIndex}><i>{lineNumber}</i>{line || " "}</span>
                );
              })}</pre>
            )}
            <p className="evidence-explanation">{item.explanation}</p>
            {(item.type === "dependency" || item.type === "config") && !item.checks.authored && !item.test_file_group && (
              <p className="not-counted-note">Found in a file you did not commit to; not counted.</p>
            )}
            <div className="verification-list">
              {Object.entries(checkLabels).filter(([key]) => key in item.checks).map(([key, label]) => {
                const passed = item.checks[key] === true;
                return (
                  <span className={passed ? "check-pass" : "check-fail"} key={key}>
                    <Check size={13} /> {label}
                  </span>
                );
              })}
            </div>
            {item.integrity && item.integrity.length > 0 && (
              <div className="integrity-list">
                <div className="integrity-title">Authorship signals · advisory, never change the level</div>
                {item.integrity.map((signal) => (
                  <div className={`integrity-row integrity-${signal.status}`} key={signal.id}>
                    {signal.status === "ok" ? <Check size={13} /> : signal.status === "warn" ? <AlertTriangle size={13} /> : <CircleHelp size={13} />}
                    <span>
                      <strong>{signal.label}</strong> {signal.detail}
                      {signal.matches?.map((match) => (
                        <a key={match.url} href={match.url} target="_blank" rel="noreferrer"> {match.repo}</a>
                      ))}
                    </span>
                  </div>
                ))}
              </div>
            )}
            <small className="commit-label">Revision {item.commit_sha}</small>
          </article>
        ))
      )}
    </section>
  );
}
