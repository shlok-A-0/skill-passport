import { AlertTriangle, ArrowUpRight, Award, ShieldCheck, Users, Wrench } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, type Passport, type Role } from "../api";
import { latestPerStudent, rankCandidates, type Verifiability } from "../verifiability";
import { skillName } from "../skillNames";

const levelOrder: Record<string, number> = {
  Claimed: 0,
  Used: 1,
  Applied: 2,
  Demonstrated: 3,
};

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
        setPassports(latestPerStudent(allPassports));
        if (availableRoles.length) setRoleId(availableRoles[0].id);
      })
      .catch((reason: Error) => setError(reason.message));
  }, []);

  const role = roles.find((item) => item.id === roleId);
  const requirements = (role?.requirements ?? []) as {
    skill_id: string;
    min_level: string;
    importance: string;
  }[];
  const rows = useMemo(() => passports.map((passport) => {
    const matched = requirements.filter((requirement) => {
      const studentSkill = passport.skills.find((skill) => skill.skill_id === requirement.skill_id);
      return Boolean(
        studentSkill
        && studentSkill.level !== "Claimed"
        && studentSkill.evidence.length
        && levelOrder[studentSkill.level] >= levelOrder[requirement.min_level],
      );
    }).length;
    return { passport, matched };
  }), [passports, requirements]);
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
      skill: skillName(requirement.skill_id),
      missing,
      percent: rows.length ? Math.round((missing / rows.length) * 100) : 0,
      skill_id: requirement.skill_id,
    };
  }).sort((left, right) => right.percent - left.percent);
  const topGap = gaps.find((gap) => gap.percent > 0);
  const demoPresent = passports.some((passport) => passport.is_demo);

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
        <section className="panel workshop-card"><span className="stat-icon orange"><Wrench size={18} /></span><div><small>SUGGESTED WORKSHOP</small><strong>{topGap ? `Build proof for ${topGap.skill}` : "Add passports to reveal shared gaps"}</strong><p>{topGap ? `${topGap.percent}% of this group has no evidence for this requirement.` : "The highest shared evidence gap appears here."}</p></div></section>
      </div>
      <section className="panel ranking-panel">
        <div className="panel-heading">
          <div><div className="eyebrow">CANDIDATE RANKING · {role?.name ?? "ROLE"}</div><h2>Who has the strongest verified proof</h2></div>
          <span className="muted">Ranked by must-have requirements met, then all requirements met, then authorship checks</span>
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
                    Meets {best.mustMet} of {best.mustTotal} must-have and {best.met} of {requirements.length} total requirements with code evidence.{" "}
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
                    <td><Link to={`/passport/${row.passport.id}`}>{row.passport.github_username}</Link>{row.passport.is_demo && <small className="demo-tag">DEMO</small>}<div className="muted">{row.verify.codeSkills} code-backed skills</div></td>
                    <td>{row.mustMet} of {row.mustTotal}</td>
                    <td>{row.met} of {requirements.length}</td>
                    <td><VerifyPill verify={row.verify} />{row.verify.copiedExcluded > 0 && <div className="copied-excluded"><AlertTriangle size={11} /> {row.verify.copiedExcluded} copied citation{row.verify.copiedExcluded === 1 ? "" : "s"} excluded</div>}</td>
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
            <p className="ranking-note">Verifiability counts advisory authorship checks (line authorship, commit history, bulk uploads, copies found elsewhere, AI chat text left in files). A warning is a prompt to look, not proof of copying. Unverified resume claims never count towards requirements.</p>
          </>
        )}
      </section>
      <div className="placement-grid">
        <section className="panel heatmap-panel">
          <div className="panel-heading"><div><div className="eyebrow">EVIDENCE BY STUDENT</div><h2>{role?.name ?? "Role"} requirements</h2></div><span className="muted">Select a row to open a passport</span></div>
          {!rows.length ? <div className="empty-state">No passports yet. Generate student passports to populate the batch view.</div> : (
            <div className="heatmap-scroll">
              <table className="heatmap-table">
                <thead><tr><th>Student</th>{requirements.map((req) => <th key={req.skill_id}>{skillName(req.skill_id)}</th>)}</tr></thead>
                <tbody>{rows.map(({ passport }) => (
                  <tr key={passport.id}>
                    <td><Link to={`/passport/${passport.id}`}>{passport.github_username}{passport.is_demo && <small>DEMO</small>}</Link></td>
                    {requirements.map((req) => {
                      const skill = passport.skills.find((item) => item.skill_id === req.skill_id);
                      const level = skill?.evidence.length ? skill.level : undefined;
                      return <td key={req.skill_id}><span className={`heat-cell ${levelClass(level)}`} title={`${req.skill_id}: ${level ?? "Missing"}`} /></td>;
                    })}
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
          <div className="heatmap-legend">
            <span><i className="heat-used" />Used</span><span><i className="heat-applied" />Applied</span><span><i className="heat-demonstrated" />Demonstrated</span><span><i className="heat-missing" />Missing</span>
          </div>
        </section>
        <section className="panel gaps-panel">
          <div className="panel-heading"><div><div className="eyebrow">SHARED LEARNING GAPS</div><h2>Most-missing requirements</h2></div></div>
          <div className="gap-chart">
            {gaps.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={gaps} layout="vertical" margin={{ top: 4, right: 14, left: 6, bottom: 4 }}>
                  <CartesianGrid stroke="#edf0f4" horizontal={false} />
                  <XAxis type="number" domain={[0, 100]} tickFormatter={(value: number) => `${value}%`} tick={{ fontSize: 10, fill: "#858b99" }} />
                  <YAxis type="category" dataKey="skill" width={100} tick={{ fontSize: 10, fill: "#525b71" }} />
                  <Tooltip formatter={(value) => [`${value}% with no evidence`, "Cohort gap"]} />
                  <Bar dataKey="percent" fill="#f26b1d" radius={[0, 5, 5, 0]} barSize={13} />
                </BarChart>
              </ResponsiveContainer>
            ) : <div className="empty-state">No role requirements to compare.</div>}
          </div>
          {topGap && <div className="gap-callout"><span className="stat-icon orange"><Wrench size={15} /></span><div><strong>Workshop opportunity: {topGap.skill}</strong><p>Consider a hands-on session that leaves students with inspectable repo evidence.</p></div><ArrowUpRight size={15} /></div>}
        </section>
      </div>
    </div>
  );
}
