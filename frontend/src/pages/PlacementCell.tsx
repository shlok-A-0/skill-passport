import { AlertTriangle, ArrowUpRight, Award, Download, ShieldCheck, Users, Wrench } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, NOT_FOUND_TOOLTIP, skillLabel, type Passport, type Role } from "../api";
import { COVERAGE_MISSING, GROWTH_UNAVAILABLE, growthUnavailable } from "../passportNotes";
import { earlierSourcesText, excludedCitations, excludedCitationsText, latestPerStudent, notWrittenText, rankCandidates, requirementsMet, type Verifiability } from "../verifiability";

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
  const [totalPassports, setTotalPassports] = useState(0);
  const [loadedPassportCount, setLoadedPassportCount] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [roleId, setRoleId] = useState("");
  const [preferredRole, setPreferredRole] = useState("");
  const [searchParams] = useSearchParams();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [retryLoadMore, setRetryLoadMore] = useState(false);

  async function loadInitial() {
    setLoading(true);
    setError("");
    setRetryLoadMore(false);
    try {
      const [availableRoles, page] = await Promise.all([api.roles(), api.reviewPassports()]);
      setRoles(availableRoles);
      // One row per student (latest passport per username) for stats, heatmap, gaps and ranking.
      setPassports(latestPerStudent(page.items));
      setTotalPassports(page.total);
      setLoadedPassportCount(page.items.length);
      if (availableRoles.length) setRoleId(availableRoles[0].id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load cohort insights.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadInitial();
  }, []);

  useEffect(() => {
    setPreferredRole(searchParams.get("preferredRole") ?? "");
  }, [searchParams]);

  async function loadMore() {
    if (loadingMore) return;
    setLoadingMore(true);
    setError("");
    setRetryLoadMore(false);
    try {
      const page = await api.reviewPassports(50, loadedPassportCount);
      setPassports((current) => latestPerStudent([...current, ...page.items]));
      setTotalPassports(page.total);
      setLoadedPassportCount((count) => count + page.items.length);
    } catch (reason) {
      setRetryLoadMore(true);
      setError(reason instanceof Error ? reason.message : "Could not load more passports.");
    } finally {
      setLoadingMore(false);
    }
  }

  const role = roles.find((item) => item.id === roleId);
  const preferredRoleOptions = [...new Set([...roles.map((item) => item.name), ...passports.map((passport) => passport.target_role)])].sort();
  const visiblePassports = preferredRole ? passports.filter((passport) => passport.target_role === preferredRole) : passports;
  const requirements = role?.requirements ?? [];
  const rows = useMemo(
    () => visiblePassports.map((passport) => ({ passport, matched: requirementsMet(passport, requirements).met })),
    [visiblePassports, requirements],
  );
  const ranked = useMemo(() => rankCandidates(visiblePassports, requirements), [visiblePassports, requirements]);
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
  const demoPresent = visiblePassports.some((passport) => passport.is_demo);

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
      "Not Written By Candidate",
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
          row.verify.notWritten,
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
        <div><h1>Skills across your cohort</h1><p>See where students have proof and where a shared workshop can help.</p></div>
        <div className="placement-filters">
          <label className="role-select-label">Role<select value={roleId} onChange={(event) => setRoleId(event.target.value)}>{roles.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          <label className="role-select-label">Preferred role<select value={preferredRole} onChange={(event) => setPreferredRole(event.target.value)}><option value="">All preferred roles</option>{preferredRoleOptions.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
        </div>
      </header>
      {demoPresent && <div className="demo-notice">Includes demo passports.</div>}
      {error && <div className="notice notice-error" role="alert">{error}<button type="button" className="link-button" onClick={() => void (retryLoadMore ? loadMore() : loadInitial())}>Retry</button></div>}
      {loading && <div className="page-message" role="status">Loading cohort insights…</div>}
      {!loading && loadedPassportCount < totalPassports && <p className="muted cohort-page-note">Role filters and exports cover the {loadedPassportCount} loaded passports so far.</p>}
      <div className="placement-stats">
        <section className="panel stat-card"><span className="stat-icon orange"><Users size={18} /></span><div><small>Students</small><strong>{visiblePassports.length}</strong>{loadedPassportCount < totalPassports && <i>{loadedPassportCount} of {totalPassports} passports loaded</i>}</div></section>
        <section className="panel stat-card"><span className="stat-icon green"><Wrench size={18} /></span><div><small>Avg. requirements met</small><strong>{avgMet} <i>of {requirements.length}</i></strong></div></section>
        <section className="panel workshop-card"><span className="stat-icon orange"><Wrench size={18} /></span><div><small>Suggested workshop</small><strong>{topGap ? `Build proof for ${topGap.skill}` : "Add passports to reveal shared gaps"}</strong><p>{topGap ? `No evidence found for ${topGap.percent}% of this group.` : "The highest shared gap appears here."}</p></div></section>
      </div>
      <section className="panel ranking-panel">
        <div className="panel-heading">
          <div><h2>Candidate ranking · {role?.name ?? "Role"}</h2></div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
            {ranked.length > 0 && (
              <button
                type="button"
                className="secondary-btn"
                style={{ padding: "0.35rem 0.75rem", fontSize: "0.8rem", display: "inline-flex", alignItems: "center", gap: "0.35rem" }}
                onClick={downloadCohortCsv}
                title="Download candidate ranking as CSV"
              >
                <Download size={13} /> Export shown CSV
              </button>
            )}
            <span className="muted">Ranked by must-haves met, then total met, then authorship checks</span>
          </div>
        </div>
        {!loading && !error && !ranked.length ? <div className="empty-state">{preferredRole ? `No loaded passports have ${preferredRole} as their preferred role.` : "No passports yet. Generate student passports to rank candidates."}</div> : ranked.length > 0 ? (
          <>
            {best && (
              <div className="best-candidate">
                <span className="stat-icon green"><Award size={18} /></span>
                <div>
                  <small>Top candidate</small>
                  <strong>{best.passport.github_username}{best.passport.is_demo && <em> DEMO DATA</em>}</strong>
                  <p>
                    Meets {best.mustMet} of {best.mustTotal} must-have and {best.met} of {requirements.length} total requirements; {best.codeMet} with code evidence (Applied or Demonstrated).{" "}
                    {best.verify.tier === "Not checked"
                      ? "Authorship checks have not run on this passport yet; use Re-check."
                      : best.verify.warnings.length
                        ? `${best.verify.warnings.length} authorship signal(s) need a human look.`
                        : excludedCitations(best.verify)
                          ? `${excludedCitationsText(excludedCitations(best.verify))}.`
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
                    <td><Link to={`/recruiter/${row.passport.id}`}>{row.passport.github_username}</Link>{row.passport.is_demo && <small className="demo-tag">DEMO</small>}<div className="muted">{row.verify.codeSkills} code-backed skills</div>
                      {growthUnavailable(row.passport) && <div className="unavailable-note">{GROWTH_UNAVAILABLE}</div>}
                      {!row.passport.coverage && <div className="unavailable-note">{COVERAGE_MISSING}</div>}</td>
                    <td>{row.mustMet} of {row.mustTotal}</td>
                    <td>{row.met} of {requirements.length}</td>
                    <td><VerifyPill verify={row.verify} />{row.verify.earlierSources > 0 && <div className="earlier-sources"><AlertTriangle size={11} /> {earlierSourcesText(row.verify.earlierSources)}</div>}{row.verify.notWritten > 0 && <div className="earlier-sources"><AlertTriangle size={11} /> {notWrittenText(row.verify.notWritten)}</div>}</td>
                    <td className="rank-flags">
                      {row.verify.tier === "Not checked" ? <span className="muted">Run Re-check to collect signals</span>
                        : row.verify.warnings.length === 0 && !excludedCitations(row.verify) ? <span className="flag-ok"><ShieldCheck size={12} /> None</span>
                          : <>
                            {excludedCitations(row.verify) > 0 && <span className="flag-warn"><AlertTriangle size={11} /> {excludedCitationsText(excludedCitations(row.verify))}</span>}
                            {row.verify.warnings.slice(0, 2).map(({ signal, where }) => (
                              <span className="flag-warn" key={`${signal.id}-${where}`} title={signal.detail}><AlertTriangle size={11} /> {signal.label}: <i>{where}</i></span>
                            ))}
                          </>}
                      {row.verify.warnings.length > 2 && <span className="muted">+{row.verify.warnings.length - 2} more</span>}
                    </td>
                    <td><Link className="view-evidence-link" to={`/recruiter/${row.passport.id}`}>Recruiter view</Link></td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
            <p className="ranking-note">Authorship warnings are prompts to look, not findings. Unverified claims never count towards requirements.</p>
          </>
        ) : null}
        {!loading && loadedPassportCount < totalPassports && <button type="button" className="secondary-btn" onClick={loadMore} disabled={loadingMore}>{loadingMore ? "Loading…" : `Load more (${totalPassports - loadedPassportCount} passports remain)`}</button>}
      </section>
      <div className="placement-grid">
        <section className="panel heatmap-panel">
          <div className="panel-heading"><div><h2>Requirements by student</h2></div><span className="muted">Select a row to open a passport</span></div>
          {!loading && !error && !rows.length ? <div className="empty-state">{preferredRole ? `No loaded passports have ${preferredRole} as their preferred role.` : "No passports yet. Generate student passports to populate the batch view."}</div> : rows.length > 0 ? (
            <div className="heatmap-scroll">
              <table className="heatmap-table">
                <thead><tr><th>Student</th>{requirements.map((req) => <th key={req.skill_id}>{skillLabel(req)}</th>)}</tr></thead>
                <tbody>{rows.map(({ passport }) => (
                  <tr key={passport.id}>
                    <td><Link to={`/recruiter/${passport.id}`}>{passport.github_username}{passport.is_demo && <small>DEMO</small>}</Link></td>
                    {requirements.map((req) => {
                      const skill = passport.skills.find((item) => item.skill_id === req.skill_id);
                      const level = skill?.evidence.length ? skill.level : undefined;
                      return <td key={req.skill_id}><span className={`heat-cell ${levelClass(level)}`} title={`${skillLabel(req)}: ${level ?? `Not found. ${NOT_FOUND_TOOLTIP}`}`} /></td>;
                    })}
                  </tr>
                ))}</tbody>
              </table>
            </div>
          ) : null}
          <div className="heatmap-legend">
            <span><i className="heat-used" />Used</span><span><i className="heat-applied" />Applied</span><span><i className="heat-demonstrated" />Demonstrated</span><span title={NOT_FOUND_TOOLTIP}><i className="heat-missing" />Not found</span>
          </div>
        </section>
        <section className="panel gaps-panel">
          <div className="panel-heading"><div><h2>Most common gaps</h2></div></div>
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
