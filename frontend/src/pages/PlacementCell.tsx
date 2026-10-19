import { AlertTriangle, ArrowUpRight, Award, Download, ShieldCheck, Users, Wrench } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, NOT_FOUND_TOOLTIP, skillLabel, type Passport, type Role } from "../api";
import { COVERAGE_MISSING, GROWTH_UNAVAILABLE, growthUnavailable } from "../passportNotes";
import { earlierSourcesText, latestPerStudent, rankCandidates, requirementsMet, type Verifiability } from "../verifiability";

function VerifyPill({ verify }: { verify: Verifiability }) {
  return (
    <span className={`verify-pill verify-${verify.tier.replace(" ", "-").toLowerCase()}`}>
      {verify.tier === "Not checked" ? "Not checked" : `${verify.tier} · ${verify.passed} of ${verify.checked} checks`}
    </span>
  );
}

function levelClass(level?: string) {
  return level ? `heat-${level.toLowerCase()}` : "heat-missing";
}

export default function PlacementCell() {
  const [roles, setRoles] = useState<Role[]>([]);
  const [passports, setPassports] = useState<Passport[]>([]);
  const [roleId, setRoleId] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([api.roles(), api.reviewPassports()])
      .then(([availableRoles, allPassports]) => {
        setRoles(availableRoles);
        // One row per student (latest passport per username) for stats, heatmap, gaps and ranking.
        setPassports(latestPerStudent(allPassports));
        if (availableRoles.length) setRoleId(availableRoles[0].id);
      })
      .catch((reason: Error) => setError(reason.message));
  }, []);

  const role = roles.find((item) => item.id === roleId);
  const requirements = role?.requirements ?? [];
  const rows = useMemo(
    () => passports.map((passport) => ({ passport, matched: requirementsMet(passport, requirements).met })),
    [passports, requirements],
  );
  const ranked = useMemo(() => rankCandidates(passports, requirements), [passports, requirements]);
  const best = ranked[0];
  const avgMet = rows.length
    ? (rows.reduce((total, row) => total + row.matched, 0) / rows.length).toFixed(1)
    : "0";
  const gaps = requirements.map((requirement) => {
    const missing = rows.filter(({ passport }) => {
      const skill = passport.skills.find((item) => item.skill_id === requirement.skill_id);
      return !skill || skill.level === "Claimed" || !skill.evidence.length;
    }).length;
    return {
      skill: skillLabel(requirement),
      missing,
      percent: rows.length ? Math.round((missing / rows.length) * 100) : 0,
      skill_id: requirement.skill_id,
    };
  }).sort((left, right) => right.percent - left.percent);
  const topGap = gaps.find((gap) => gap.percent > 0);
  const demoPresent = passports.some((passport) => passport.is_demo);

  const downloadCohortCsv = () => {
    if (!ranked.length) return;
    const headers = [
      "Rank",
      "Candidate",
      "Is Demo",
      "Must-Have Met",
      "Must-Have Total",
      "All Met",
      "Total Requirements",
      "Code-Backed Skills",
      "Verifiability Tier",
      "Checks Passed",
      "Checks Total",
      "Earlier Sources",
      "Warnings Count",
    ];
    const csvRows = [headers.join(",")];
    ranked.forEach((row, idx) => {
      csvRows.push(
        [
          idx + 1,
          `"${row.passport.github_username}"`,
          row.passport.is_demo ? "Yes" : "No",
          row.mustMet,
          row.mustTotal,
          row.met,
          requirements.length,
          row.verify.codeSkills,
          `"${row.verify.tier}"`,
          row.verify.passed,
          row.verify.checked,
          row.verify.earlierSources,
          row.verify.warnings.length,
        ].join(",")
      );
    });
    const blob = new Blob([csvRows.join("\n")], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.setAttribute("href", url);
    link.setAttribute("download", `cohort_${role?.id ?? "candidates"}_report.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  return (
    <div className="placement-page">
      <header className="placement-heading">
        <div><div className="eyebrow">PLACEMENT CELL · BATCH INSIGHTS</div><h1>Skills across your cohort</h1><p>Evidence ladders show where students have proof and where a shared workshop can help.</p></div>
        <label className="role-select-label">TARGET ROLE<select value={roleId} onChange={(event) => setRoleId(event.target.value)}>{roles.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      </header>
      {demoPresent && <div className="demo-notice">DEMO DATA is included in this cohort view.</div>}
      {error && <div className="notice notice-error">{error}</div>}
      <div className="placement-stats">
        <section className="panel stat-card"><span className="stat-icon orange"><Users size={18} /></span><div><small>STUDENTS IN VIEW</small><strong>{passports.length}</strong></div></section>
        <section className="panel stat-card"><span className="stat-icon green"><Wrench size={18} /></span><div><small>AVERAGE REQUIREMENTS MET</small><strong>{avgMet} <i>of {requirements.length} requirements</i></strong></div></section>
        <section className="panel workshop-card"><span className="stat-icon orange"><Wrench size={18} /></span><div><small>SUGGESTED WORKSHOP</small><strong>{topGap ? `Build proof for ${topGap.skill}` : "Add passports to reveal shared gaps"}</strong><p>{topGap ? `For ${topGap.percent}% of this group, no evidence was found in the analysed files.` : "The highest shared evidence gap appears here."}</p></div></section>
      </div>
      <section className="panel ranking-panel">
        <div className="panel-heading">
          <div><div className="eyebrow">CANDIDATE RANKING · {role?.name ?? "ROLE"}</div><h2>Who has the strongest evidence</h2></div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
            {ranked.length > 0 && (
              <button
                type="button"
                className="secondary-btn"
                style={{ padding: "0.35rem 0.75rem", fontSize: "0.8rem", display: "inline-flex", alignItems: "center", gap: "0.35rem" }}
                onClick={downloadCohortCsv}
                title="Download candidate ranking as CSV"
              >
                <Download size={13} /> Export CSV
              </button>
            )}
            <span className="muted">Ranked by must-have requirements met, then all requirements met, then authorship checks</span>
          </div>
        </div>
        {!ranked.length ? <div className="empty-state">No passports yet. Generate student passports to rank candidates.</div> : (
          <>
            {best && (
              <div className="best-candidate">
                <span className="stat-icon green"><Award size={18} /></span>
                <div>
                  <small>STRONGEST CANDIDATE</small>
                  <strong>{best.passport.github_username}{best.passport.is_demo && <em> DEMO DATA</em>}</strong>
                  <p>
                    Meets {best.mustMet} of {best.mustTotal} must-have and {best.met} of {requirements.length} total requirements; {best.codeMet} with code evidence (Applied or Demonstrated).{" "}
                    {best.verify.tier === "Not checked"
                      ? "Authorship checks have not run on this passport yet; use Re-check."
                      : best.verify.warnings.length
                        ? `${best.verify.warnings.length} authorship signal(s) need a human look.`
                        : `All ${best.verify.checked} authorship checks passed.`}
                  </p>
                </div>
                <Link className="best-link" to={`/recruiter/${best.passport.id}`}>Recruiter view <ArrowUpRight size={14} /></Link>
              </div>
            )}
            <div className="ranking-scroll">
              <table className="ranking-table">
                <thead><tr><th>#</th><th>Candidate</th><th>Must-have met</th><th>All requirements</th><th>Verifiability</th><th>Needs a human look</th><th /></tr></thead>
                <tbody>{ranked.map((row, index) => (
                  <tr key={row.passport.id} className={index === 0 ? "rank-top" : ""}>
                    <td className="rank-number">{index + 1}</td>
                    <td><Link to={`/passport/${row.passport.id}`}>{row.passport.github_username}</Link>{row.passport.is_demo && <small className="demo-tag">DEMO</small>}<div className="muted">{row.verify.codeSkills} code-backed skills</div>
                      {growthUnavailable(row.passport) && <div className="unavailable-note">{GROWTH_UNAVAILABLE}</div>}
                      {!row.passport.coverage && <div className="unavailable-note">{COVERAGE_MISSING}</div>}</td>
                    <td>{row.mustMet} of {row.mustTotal}</td>
                    <td>{row.met} of {requirements.length}</td>
                    <td><VerifyPill verify={row.verify} />{row.verify.earlierSources > 0 && <div className="earlier-sources"><AlertTriangle size={11} /> {earlierSourcesText(row.verify.earlierSources)}</div>}</td>
                    <td className="rank-flags">
                      {row.verify.tier === "Not checked" ? <span className="muted">Run Re-check to collect signals</span>
                        : row.verify.warnings.length === 0 ? <span className="flag-ok"><ShieldCheck size={12} /> None</span>
                          : row.verify.warnings.slice(0, 2).map(({ signal, where }) => (
                            <span className="flag-warn" key={`${signal.id}-${where}`} title={signal.detail}><AlertTriangle size={11} /> {signal.label}: <i>{where}</i></span>
                          ))}
                      {row.verify.warnings.length > 2 && <span className="muted">+{row.verify.warnings.length - 2} more</span>}
                    </td>
                    <td><Link className="view-evidence-link" to={`/recruiter/${row.passport.id}`}>Recruiter view</Link></td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
            <p className="ranking-note">Verifiability counts advisory authorship checks (line authorship, commit history, bulk uploads, matching code in earlier public sources, AI chat text left in files). A warning is a prompt to look, not a finding. Unverified resume claims never count towards requirements.</p>
          </>
        )}
      </section>
      <div className="placement-grid">
        <section className="panel heatmap-panel">
          <div className="panel-heading"><div><div className="eyebrow">EVIDENCE BY STUDENT</div><h2>{role?.name ?? "Role"} requirements</h2></div><span className="muted">Select a row to open a passport</span></div>
          {!rows.length ? <div className="empty-state">No passports yet. Generate student passports to populate the batch view.</div> : (
            <div className="heatmap-scroll">
              <table className="heatmap-table">
                <thead><tr><th>Student</th>{requirements.map((req) => <th key={req.skill_id}>{skillLabel(req)}</th>)}</tr></thead>
                <tbody>{rows.map(({ passport }) => (
                  <tr key={passport.id}>
                    <td><Link to={`/passport/${passport.id}`}>{passport.github_username}{passport.is_demo && <small>DEMO</small>}</Link></td>
                    {requirements.map((req) => {
                      const skill = passport.skills.find((item) => item.skill_id === req.skill_id);
                      const level = skill?.evidence.length ? skill.level : undefined;
                      return <td key={req.skill_id}><span className={`heat-cell ${levelClass(level)}`} title={`${skillLabel(req)}: ${level ?? `Not found. ${NOT_FOUND_TOOLTIP}`}`} /></td>;
                    })}
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
          <div className="heatmap-legend">
            <span><i className="heat-used" />Used</span><span><i className="heat-applied" />Applied</span><span><i className="heat-demonstrated" />Demonstrated</span><span title={NOT_FOUND_TOOLTIP}><i className="heat-missing" />Not found</span>
          </div>
        </section>
        <section className="panel gaps-panel">
          <div className="panel-heading"><div><div className="eyebrow">SHARED LEARNING GAPS</div><h2>Requirements most often not found</h2></div></div>
          <div className="gap-chart">
            {gaps.length ? (
              <div className="gap-bars-list">
                {gaps.map((item) => (
                  <div key={item.skill} className="gap-bar-row">
                    <span className="gap-bar-label" title={item.skill}>{item.skill}</span>
                    <div className="gap-bar-track">
                      <div
                        className="gap-bar-fill"
                        style={{ width: `${item.percent}%` }}
                        title={`${item.percent}% not found in analysed files`}
                      />
                    </div>
                    <span className="gap-bar-val">{item.percent}%</span>
                  </div>
                ))}
              </div>
            ) : <div className="empty-state">No role requirements to compare.</div>}
          </div>
          {topGap && <div className="gap-callout"><span className="stat-icon orange"><Wrench size={15} /></span><div><strong>Workshop opportunity: {topGap.skill}</strong><p>Consider a hands-on session that leaves students with inspectable repo evidence.</p></div><ArrowUpRight size={15} /></div>}
        </section>
      </div>
    </div>
  );
}
