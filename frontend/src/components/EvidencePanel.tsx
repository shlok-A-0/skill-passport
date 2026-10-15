import { AlertTriangle, Check, CircleHelp, ExternalLink, FileCode2, X } from "lucide-react";
import { skillLabel, type Evidence, type PassportSkill } from "../api";
import LadderPill from "./LadderPill";
import type { Audience } from "./RejectedClaims";

const checkLabels: Record<string, string> = {
  file_exists: "File exists in repository",
  lines_valid: "Cited lines are valid",
  authored: "Authored by you",
  not_fork: "Repository is not a fork",
  signature_found: "Skill signature found in code",
};
const GIT_HISTORY = "(commit history)";
const CODE_CHECKS = ["file_exists", "lines_valid", "authored", "not_fork", "signature_found"];
const FILE_CHECKS = ["file_exists", "authored", "not_fork"];

/** Only the checks that apply to this kind of evidence, so nothing shows a red ✗ that was never checked. */
function applicableChecks(item: Evidence): string[] {
  const keys = item.type === "code" ? CODE_CHECKS
    : item.test_file_group || item.type === "commit" || item.file_path === GIT_HISTORY ? Object.keys(checkLabels)
      : FILE_CHECKS;
  return keys.filter((key) => key in item.checks);
}

function checkLabel(item: Evidence, key: string): string {
  return item.file_path === GIT_HISTORY && key === "file_exists" ? "Commit history found" : checkLabels[key];
}

export default function EvidencePanel({ skill, audience }: { skill: PassportSkill; audience: Audience }) {
  return (
    <section className="panel evidence-panel">
      <div className="panel-heading">
        <div>
          <div className="eyebrow">EVIDENCE TRAIL</div>
          <h2>{skillLabel(skill)}</h2>
        </div>
        <LadderPill level={skill.level} />
      </div>
      {skill.certified && <span className="certified-tag">Certificate noted · does not raise level</span>}
      {skill.evidence.length === 0 ? (
        <div className="empty-evidence">No evidence for this skill was found in the analysed files.</div>
      ) : (
        skill.evidence.map((item, index) => (
          <article className="evidence-item" key={`${item.url}-${index}`}>
            <div className="evidence-meta">
              <span className="file-icon"><FileCode2 size={16} /></span>
              <div>
                <strong>{item.repo}</strong>
                <div className="muted">{item.type === "commit" ? `Commit history · ${item.commit_sha.slice(0, 7)}` : item.file_path === GIT_HISTORY ? "Commit history" : item.start_line > 0 ? `${item.file_path} · lines ${item.start_line}–${item.end_line}` : item.file_path}</div>
              </div>
              <a className="icon-link" href={item.url} target="_blank" rel="noreferrer" aria-label="Open on GitHub">
                {item.type === "commit" ? "Open commit" : item.file_path === GIT_HISTORY ? "Open commits" : "Open on GitHub"} <ExternalLink size={14} />
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
            {(item.type === "dependency" || item.type === "config") && "authored" in item.checks && !item.checks.authored && !item.test_file_group && (
              <p className="not-counted-note">Found in a file you did not commit to; not counted.</p>
            )}
            <div className="verification-list">
              {applicableChecks(item).map((key) => {
                const passed = item.checks[key] === true;
                return (
                  <span className={passed ? "check-pass" : "check-fail"} key={key}>
                    {passed ? <Check size={13} /> : <X size={13} />} {checkLabel(item, key)}
                  </span>
                );
              })}
            </div>
            {audience === "recruiter" && item.integrity && item.integrity.length > 0 && (
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
