import { AlertTriangle, Check, CircleHelp, ExternalLink, FileCode2, FlaskConical, X } from "lucide-react";
import { DEFAULT_CHECK_LABELS, skillLabel, type Evidence, type PassportSkill } from "../api";
import { showsNotCountedNote } from "../evidencePanel";
import LadderPill from "./LadderPill";
import type { Audience } from "./RejectedClaims";

const GIT_HISTORY = "(commit history)";
const CODE_CHECKS = ["file_exists", "lines_valid", "authored", "not_fork", "signature_found"];
const FILE_CHECKS = ["file_exists", "authored", "not_fork"];

/** Only the checks that apply to this kind of evidence, so nothing shows a red ✗ that was never checked. */
function applicableChecks(item: Evidence): string[] {
  const keys = item.type === "code" ? CODE_CHECKS
    : item.test_file_group || item.type === "commit" || item.file_path === GIT_HISTORY ? CODE_CHECKS
      : FILE_CHECKS;
  return keys.filter((key) => key in item.checks);
}

/** "Cited lines written by you: N of M": ✗ for none, neutral below half or when unknown. */
function LinesWrittenRow({ item, label }: { item: Evidence; label: string }) {
  const share = item.lines_written;
  if (!share) return <span className="check-neutral"><CircleHelp size={13} /> {label}: Unknown</span>;
  const text = `${label}: ${share.written} of ${share.cited}`;
  if (share.written === 0) return <span className="check-fail"><X size={13} /> {text}</span>;
  if (share.written * 2 < share.cited) return <span className="check-neutral"><CircleHelp size={13} /> {text}</span>;
  return <span className="check-pass"><Check size={13} /> {text}</span>;
}

export default function EvidencePanel({ skill, audience, labels }: {
  skill: PassportSkill; audience: Audience; labels?: Record<string, string>;
}) {
  const label = (item: Evidence, key: string) => (
    item.file_path === GIT_HISTORY && key === "file_exists" ? "Commit history found" : labels?.[key] ?? DEFAULT_CHECK_LABELS[key]
  );
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
            {item.demonstrated_by && (
              <p className="evidence-context evidence-tested"><FlaskConical size={12} /> Exercised by tests: {item.demonstrated_by.path} ({item.demonstrated_by.match === "name" ? "test named after this file" : "test imports this file"})</p>
            )}
            {item.build_config_present && <p className="evidence-context">Build or deploy config present</p>}
            {/* Grouped test files count only when committed to, like other config files (2026-10-20). */}
            {showsNotCountedNote(item) && (
              <p className="not-counted-note">Found in a file you did not commit to; not counted.</p>
            )}
            <div className="verification-list">
              {applicableChecks(item).map((key) => {
                const passed = item.checks[key] === true;
                return (
                  <span className={passed ? "check-pass" : "check-fail"} key={key}>
                    {passed ? <Check size={13} /> : <X size={13} />} {label(item, key)}
                  </span>
                );
              })}
              {item.type === "code" && <LinesWrittenRow item={item} label={label(item, "lines_written")} />}
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
            {audience === "recruiter" && item.record && (
              <details className="evidence-record">
                <summary>Evidence record</summary>
                <dl>
                  <dt>Commit</dt><dd>{item.record.commit_sha}</dd>
                  <dt>Lines</dt><dd>{item.record.path} · L{item.record.start_line}–L{item.record.end_line}</dd>
                  <dt>Snippet SHA-256</dt><dd>{item.record.snippet_sha256}</dd>
                  <dt>Retrieved</dt><dd>{item.record.retrieved_at ?? "unknown"}</dd>
                  <dt>Verifier version</dt><dd>{item.record.verifier_version}</dd>
                  <dt>Checks</dt><dd>{Object.entries(item.record.checks).map(([key, passed]) => `${key}: ${passed ? "pass" : "fail"}`).join(", ")}</dd>
                  <dt>Rules applied</dt><dd>{item.record.rules_applied.join(", ")}</dd>
                </dl>
              </details>
            )}
            <small className="commit-label">Revision {item.commit_sha}</small>
          </article>
        ))
      )}
    </section>
  );
}
