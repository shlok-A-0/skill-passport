import { ChevronDown, FolderSearch } from "lucide-react";
import { useState } from "react";
import type { Coverage } from "../api";
import { COVERAGE_MISSING } from "../passportNotes";

const skipLabels: Record<string, string> = {
  too_large: "too large to read",
  too_many_lines: "longer than the line limit",
  unreadable: "could not be fetched",
  empty: "empty",
  file_limit: "not read (file limit per repository)",
};

function about(lines: number): string {
  return lines >= 1000 ? `about ${(Math.round(lines / 100) * 100).toLocaleString()}` : String(lines);
}

/** One-line summary of what was analysed; click to see the per-repository breakdown. */
export default function CoverageSummary({ coverage }: { coverage?: Coverage }) {
  const [open, setOpen] = useState(false);
  // Passports built before coverage was recorded.
  if (!coverage) return <div className="coverage"><span className="coverage-line coverage-missing"><FolderSearch size={13} /> {COVERAGE_MISSING}</span></div>;
  const repos = coverage.repos_analysed === 1 ? "repository" : "repositories";
  return (
    <div className={`coverage ${open ? "coverage-open" : ""}`}>
      <button type="button" className="coverage-line" onClick={() => setOpen(!open)} aria-expanded={open}
        title="Evidence comes from a sample of files. Click for details.">
        <FolderSearch size={13} /> Analysed {coverage.repos_analysed} {repos} · {coverage.source_files_read} of {coverage.source_files_total} source files · {about(coverage.lines_read)} lines
        <ChevronDown size={13} className="coverage-chevron" />
      </button>
      {open && (
        <div className="coverage-detail">
          <table>
            <thead><tr><th>Repository</th><th>Files read</th><th>Lines</th><th>Not read</th></tr></thead>
            <tbody>{coverage.repos.map((repo) => (
              <tr key={repo.name}>
                <td>{repo.name}</td>
                <td>{repo.source_files_read} of {repo.source_files_total}</td>
                <td>{repo.lines_read.toLocaleString()}</td>
                <td>{Object.entries(repo.skipped).map(([reason, count]) => `${count} ${skipLabels[reason] ?? reason}`).join(" · ") || "—"}
                  {repo.files_dropped_by_line_cap > 0 && ` · ${repo.files_dropped_by_line_cap} left out of AI proposals (line limit)`}</td>
              </tr>
            ))}</tbody>
          </table>
          {coverage.repos_skipped.length > 0 && (
            <p><strong>Repositories not analysed:</strong> {coverage.repos_skipped.map((repo) => `${repo.name} (${repo.reason})`).join(", ")}</p>
          )}
          {coverage.notes.map((note, index) => <p key={`${index}-${note}`}>{note}</p>)}
          <p className="muted">Skills not found here may still be in files that weren't read.</p>
        </div>
      )}
    </div>
  );
}
