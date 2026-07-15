import { useEffect, useState } from "react";
import { DEFAULT_TAB, tabs, type TabId } from "../tabRegistry";
import { AppShell } from "./layout/AppShell";

export function Dashboard() {
  const [activeTab, setActiveTab] = useState<TabId>(DEFAULT_TAB);
  const [visitedTabs, setVisitedTabs] = useState<Set<TabId>>(() => new Set([DEFAULT_TAB]));

  useEffect(() => {
    setVisitedTabs((current) => (current.has(activeTab) ? current : new Set(current).add(activeTab)));
  }, [activeTab]);

  return (
    <AppShell activeTab={activeTab} onTabChange={setActiveTab}>
      {/* Keep visited tabs mounted so switching tabs preserves local state such as chat history. */}
      {tabs.map(({ component: TabComponent, id }) => {
        if (!visitedTabs.has(id)) {
          return null;
        }

        return (
          <div className={activeTab === id ? "" : "hidden"} key={id}>
            <TabComponent />
          </div>
        );
      })}
    </AppShell>
  );
}
