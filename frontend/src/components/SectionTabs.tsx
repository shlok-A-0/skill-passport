import type { LucideIcon } from "lucide-react";
import { useEffect, useRef } from "react";

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
  idPrefix?: string;
};

/** Compact segmented action bar: one visible section at a time, everything reachable in one tap. */
export default function SectionTabs({ tabs, active, onChange, ariaLabel, idPrefix = "section" }: SectionTabsProps) {
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const stripRef = useRef<HTMLDivElement | null>(null);

  // Keep the selected pill fully inside the strip's bounds by scrolling the
  // strip only (scrollLeft). Page X/Y scroll never moves.
  useEffect(() => {
    const strip = stripRef.current;
    const index = tabs.findIndex((tab) => tab.id === active);
    const selected = index >= 0 ? tabRefs.current[index] : null;
    if (!strip || !selected) return;
    const target = selected.offsetLeft - (strip.clientWidth - selected.offsetWidth) / 2;
    strip.scrollLeft = Math.max(0, target);
  }, [active, tabs]);

  function onKeyDown(event: React.KeyboardEvent, index: number) {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft" && event.key !== "Home" && event.key !== "End") return;
    event.preventDefault();
    const last = tabs.length - 1;
    const next =
      event.key === "Home" ? 0
      : event.key === "End" ? last
      : event.key === "ArrowRight" ? (index + 1) % tabs.length
      : (index - 1 + tabs.length) % tabs.length;
    tabRefs.current[next]?.focus({ preventScroll: true });
    onChange(tabs[next].id);
  }

  return (
    <div className="section-tabs" role="tablist" aria-label={ariaLabel} ref={stripRef}>
      {tabs.map((tab, index) => {
        const Icon = tab.icon;
        const selected = tab.id === active;
        return (
          <button
            key={tab.id}
            id={`${idPrefix}-tab-${tab.id}`}
            aria-controls={`${idPrefix}-panel-${tab.id}`}
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
