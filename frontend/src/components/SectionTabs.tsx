import type { LucideIcon } from "lucide-react";
import { useRef } from "react";

export type SectionTabItem = {
  id: string;
  label: string;
  icon: LucideIcon;
  count?: number;
};

type SectionTabsProps = {
  tabs: SectionTabItem[];
  active: string;
  onChange: (id: string) => void;
  ariaLabel: string;
};

/** Compact segmented action bar: one visible section at a time, everything reachable in one tap. */
export default function SectionTabs({ tabs, active, onChange, ariaLabel }: SectionTabsProps) {
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  function onKeyDown(event: React.KeyboardEvent, index: number) {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft" && event.key !== "Home" && event.key !== "End") return;
    event.preventDefault();
    const last = tabs.length - 1;
    const next =
      event.key === "Home" ? 0
      : event.key === "End" ? last
      : event.key === "ArrowRight" ? (index + 1) % tabs.length
      : (index - 1 + tabs.length) % tabs.length;
    tabRefs.current[next]?.focus();
    onChange(tabs[next].id);
  }

  return (
    <div className="section-tabs" role="tablist" aria-label={ariaLabel}>
      {tabs.map((tab, index) => {
        const Icon = tab.icon;
        const selected = tab.id === active;
        return (
          <button
            key={tab.id}
            ref={(element) => { tabRefs.current[index] = element; }}
            type="button"
            role="tab"
            aria-selected={selected}
            tabIndex={selected ? 0 : -1}
            className={`section-tab${selected ? " section-tab-active" : ""}`}
            onClick={() => onChange(tab.id)}
            onKeyDown={(event) => onKeyDown(event, index)}
          >
            <Icon size={14} aria-hidden="true" />
            <span>{tab.label}</span>
            {typeof tab.count === "number" && <span className="section-tab-count">{tab.count}</span>}
          </button>
        );
      })}
    </div>
  );
}
