import { AlertTriangle, ArrowLeft, RefreshCw } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, type Passport, type PassportSkill } from "../api";
import SkillName from "../components/SkillName";
import CoverageSummary from "../components/CoverageSummary";
import EvidencePanel from "../components/EvidencePanel";
import IdentityChecks from "../components/IdentityChecks";
import LadderPill from "../components/LadderPill";
import RejectedClaims from "../components/RejectedClaims";
import SqlDepthPanel from "../components/SqlDepthPanel";
import { earlierSourcesText, notWrittenText, verifiability } from "../verifiability";

const levelOrder: Record<PassportSkill["level"], number> = { Claimed: 0, Used: 1, Applied: 2, Demonstrated: 3 };

/** Full evidence review for recruiters: every authorship signal and every excluded claim. */
export default function RecruiterEvidence() {
  const { id = "" } = useParams();
  const [searchParams] = useSearchParams();
  const [passport, setPassport] = useState<Passport | null>(null);
  const [selected, setSelected] = useState<PassportSkill | null>(null);
  const [error, setError] = useState("");
  const loadVersion = useRef(0);
  const activeId = useRef(id);
  useLayoutEffect(() => { activeId.current = id; }, [id]);

  useEffect(() => {
    const version = ++loadVersion.current;
    setPassport(null);
    setSelected(null);
    setError("");
    api.reviewPassport(id)
      .then((data) => {
        if (version !== loadVersion.current) return;
        setPassport(data);
        const requested = data.skills.find((skill) => skill.skill_id === searchParams.get("skill"));
        setSelected(requested ?? sortedSkills(data)[0] ?? null);
      })
      .catch((reason: Error) => { if (version === loadVersion.current) setError(reason.message); });
    return () => { loadVersion.current += 1; };
  }, [id, searchParams]);

  if (error && (!passport || passport.id !== id)) return <div className="page-message notice-error">{error}</div>;
  if (!passport || passport.id !== id) return <div className="page-message"><span className="spin"><RefreshCw size={18} /></span> Loading evidence review…</div>;

  const verify = verifiability(passport);
  return (
    <div className="passport-page">
      <header className="hero">
        <div className="hero-copy">
          <div className="hero-eyebrow">Evidence review · Not shown to the candidate</div>
          <h1>{passport.github_username}{passport.is_demo && <span className="demo-badge">DEMO DATA</span>}</h1>
          <p className="hero-sub"><span className="lead">Candidate for {passport.target_role}.</span>{" "}
            <span className={`verify-pill verify-${verify.tier.replace(" ", "-").toLowerCase()}`}>Verifiability: {verify.tier === "Not checked" ? "not checked" : `${verify.tier} · ${verify.passed} of ${verify.checked} checks`}</span>
            {verify.earlierSources > 0 && <span className="earlier-sources"><AlertTriangle size={11} /> {earlierSourcesText(verify.earlierSources)}</span>}
            {verify.notWritten > 0 && <span className="earlier-sources"><AlertTriangle size={11} /> {notWrittenText(verify.notWritten)}</span>}
          </p>
          <CoverageSummary coverage={passport.coverage} />
          <div className="hero-actions">
            <Link className="button button-light" to={`/recruiter/${passport.id}`}><ArrowLeft size={15} /> Recruiter summary</Link>
          </div>
        </div>
        <div className="hero-side">
          <section className="panel">
            <h3>Account checks</h3>
            <div className="panel-list">
              {passport.identity?.length
                ? <IdentityChecks signals={passport.identity} />
                : <div className="snapshot-row"><span>No account checks recorded</span><b>—</b></div>}
            </div>
          </section>
        </div>
      </header>
      <div className="facts">
        <div className="fact"><span>Target role</span><b>{passport.target_role}</b></div>
        <div className="fact"><span>Skills</span><b>{passport.skills.length}</b></div>
        <div className="fact"><span>Evidence records</span><b>{passport.skills.reduce((sum, skill) => sum + skill.evidence.length, 0)}</b></div>
        <div className="fact"><span>Verifiability</span><b>{verify.tier === "Not checked" ? "Not checked" : `${verify.tier} · ${verify.passed} of ${verify.checked}`}</b></div>
      </div>
      <div className="passport-grid">
        <div className="skill-column">
          <section className="panel skill-panel">
            <div className="panel-heading"><div><h2>All skills</h2></div><span className="count-tag">{passport.skills.length} skills</span></div>
            {passport.skills.length ? (
              <div className="skill-grid">{sortedSkills(passport).map((skill) => {
                const flags = skill.evidence.flatMap((item) => item.integrity ?? []).filter((signal) => signal.status === "warn").length;
                return (
                  <button className={`skill-tile ${selected?.skill_id === skill.skill_id ? "skill-selected" : ""}`} key={skill.skill_id} onClick={() => setSelected(skill)}>
                    <span className="skill-name"><SkillName item={skill} /></span><LadderPill level={skill.level} />
                    <small>{skill.evidence.length} evidence{flags > 0 ? ` · ${flags} to check` : ""}</small>
                  </button>
                );
              })}</div>
            ) : <div className="empty-state">No skills were found in this passport.</div>}
          </section>
          <SqlDepthPanel result={passport.sql_depth} audience="recruiter" />
          <RejectedClaims claims={passport.rejected_claims} audience="recruiter" passportId={passport.id}
            requests={passport.review_requests} labels={passport.check_labels}
            onChanged={() => {
              if (passport.id !== activeId.current) return;
              const version = ++loadVersion.current;
              api.reviewPassport(id)
                .then((data) => {
                  if (version !== loadVersion.current) return;
                  setPassport(data);
                })
                .catch((reason: Error) => { if (version === loadVersion.current) setError(reason.message); });
            }} />
        </div>
        <aside className="evidence-column">
          {selected ? <EvidencePanel skill={selected} audience="recruiter" labels={passport.check_labels} /> :<section className="panel empty-evidence">Select a skill to inspect its evidence trail.</section>}
        </aside>
      </div>
    </div>
  );
}

function sortedSkills(passport: Passport): PassportSkill[] {
  return [...passport.skills].sort((left, right) => levelOrder[right.level] - levelOrder[left.level]);
            }
