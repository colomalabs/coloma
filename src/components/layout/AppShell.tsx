import { useEffect, useState, type ReactNode } from "react";
import { Menu } from "lucide-react";
import type { TabId } from "../../tabRegistry";
import { Sidebar } from "./Sidebar";

type AppShellProps = {
  activeTab: TabId;
  children: ReactNode;
  onTabChange: (tab: TabId) => void;
};

export function AppShell({ activeTab, children, onTabChange }: AppShellProps) {
  const [mobileNavigationOpen, setMobileNavigationOpen] = useState(false);

  useEffect(() => {
    setMobileNavigationOpen(false);
  }, [activeTab]);

  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground md:flex-row">
      <button
        aria-label="Open navigation"
        className="fixed left-3 top-3 z-30 rounded-md border bg-background p-2 text-muted-foreground shadow-sm hover:bg-muted hover:text-foreground md:hidden"
        onClick={() => setMobileNavigationOpen(true)}
        type="button"
      >
        <Menu className="h-5 w-5" />
      </button>
      <Sidebar
        activeTab={activeTab}
        mobileOpen={mobileNavigationOpen}
        onClose={() => setMobileNavigationOpen(false)}
        onTabChange={onTabChange}
      />
      <main className="min-w-0 flex-1 pt-14 md:pt-0">{children}</main>
    </div>
  );
}
