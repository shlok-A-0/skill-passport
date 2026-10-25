import { skillLabel } from "../api";
import { Database } from "lucide-react";

/**
 * Skill label with a locally bundled language/tech logo to its left.
 * Logos live in src/assets/lang-logos (devicon, MIT) and are bundled at build
 * time - nothing is fetched from the network at runtime. Skills with no
 * matching logo render as plain text.
 */
const modules = import.meta.glob("../assets/lang-logos/*.svg", { eager: true, query: "?url", import: "default" }) as Record<string, string>;
const LOGOS: Record<string, string> = {};
for (const [path, url] of Object.entries(modules)) {
  const key = path.split("/").pop()!.replace(".svg", "");
  LOGOS[key] = url;
}

/** Normalized-name aliases -> bundled logo key. */
const ALIASES: Record<string, string> = {
  python: "python", python3: "python",
  javascript: "javascript", js: "javascript", ecmascript: "javascript",
  typescript: "typescript", ts: "typescript",
  java: "java", c: "c", "c++": "cplusplus", cpp: "cplusplus", cplusplus: "cplusplus",
  "c#": "csharp", csharp: "csharp", dotnet: "csharp",
  go: "go", golang: "go", rust: "rust", ruby: "ruby", php: "php",
  swift: "swift", kotlin: "kotlin", scala: "scala", r: "r", dart: "dart",
  perl: "perl", haskell: "haskell", elixir: "elixir", lua: "lua", matlab: "matlab",
  bash: "bash", shell: "bash", shellscripting: "bash", zsh: "bash",
  html: "html5", html5: "html5", css: "css3", css3: "css3",
  react: "react", reactjs: "react", nextjs: "nextjs", vue: "vuejs", vuejs: "vuejs",
  angular: "angular", angularjs: "angular", svelte: "svelte",
  node: "nodejs", nodejs: "nodejs", express: "nodejs",
  flutter: "flutter",
  docker: "docker", kubernetes: "kubernetes", k8s: "kubernetes",
  git: "git", github: "git", gitlab: "git", linux: "linux", nginx: "nginx",
  postgresql: "postgresql", postgres: "postgresql", mysql: "mysql",
  mongodb: "mongodb", mongo: "mongodb", redis: "redis", sqlite: "sqlite",
  graphql: "graphql", terraform: "terraform",
  django: "django", flask: "flask", fastapi: "fastapi",
  spring: "spring", springboot: "spring", rails: "rails", rubyonrails: "rails",
  aws: "aws", amazonwebservices: "aws", gcp: "googlecloud", googlecloud: "googlecloud", azure: "azure",
};

/** Keys that should show a generic database glyph instead of a product logo. */
const GENERIC_DB = new Set(["sql", "database", "databases", "relationaldatabases"]);

function normalize(value: string): string {
  return value.toLowerCase().replace(/[.\s_\/-]+/g, "");
}

export function skillLogoKey(item: { skill_id: string; name?: string }): string | null {
  for (const raw of [item.name ?? "", item.skill_id]) {
    const norm = normalize(raw.replace(/-/g, " "));
    if (!norm) continue;
    if (GENERIC_DB.has(norm)) return "__db__";
    if (ALIASES[norm]) return ALIASES[norm];
    // substring fallback for labels like "Python scripting" or "SQL (PostgreSQL)"
    for (const [alias, key] of Object.entries(ALIASES)) {
      if (alias.length >= 3 && norm.includes(alias)) return key;
    }
    if (norm.includes("sql")) return "__db__";
  }
  return null;
}

export default function SkillName({ item, className, reserveSlot = true }: { item: { skill_id: string; name?: string }; className?: string; reserveSlot?: boolean }) {
  const key = skillLogoKey(item);
  return (
    <span className={`skill-name-wrap${className ? ` ${className}` : ""}`}>
      {key === "__db__" && <Database className="skill-logo skill-logo-icon" size={14} aria-hidden="true" />}
      {key && key !== "__db__" && LOGOS[key] && <img className="skill-logo" src={LOGOS[key]} alt="" aria-hidden="true" />}
      {!key && reserveSlot && <span className="skill-logo skill-logo-empty" aria-hidden="true" />}
      <span className="skill-name-text">{skillLabel(item)}</span>
    </span>
  );
}
