import { Github, Mail, X } from "lucide-react";
import { tabGroups, type TabId } from "../../tabRegistry";
import { EndpointStatus } from "./EndpointStatus";
import { GpuStatus } from "./GpuStatus";

type SidebarProps = {
  activeTab: TabId;
  mobileOpen: boolean;
  onClose: () => void;
  onTabChange: (tab: TabId) => void;
};

function XIcon() {
  return (
    <svg aria-hidden="true" className="h-5 w-5" fill="currentColor" viewBox="0 0 24 24">
      <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z" />
    </svg>
  );
}

function SocialLinks() {
  return (
    <div className="flex w-full items-center justify-center gap-1 text-muted-foreground">
      <a
        aria-label="GitHub repository"
        className="rounded-md p-1.5 hover:bg-muted hover:text-foreground"
        href="https://github.com/colomalabs/coloma.git"
        rel="noreferrer"
        target="_blank"
      >
        <Github className="h-5 w-5" />
      </a>
      <a
        aria-label="X profile"
        className="rounded-md p-1.5 hover:bg-muted hover:text-foreground"
        href="https://x.com/tschillaciML"
        rel="noreferrer"
        target="_blank"
      >
        <XIcon />
      </a>
      <a
        aria-label="Email"
        className="rounded-md p-1.5 hover:bg-muted hover:text-foreground"
        href="mailto:hello@colomalabs.ai"
      >
        <Mail className="h-5 w-5" />
      </a>
    </div>
  );
}

export function Sidebar({ activeTab, mobileOpen, onClose, onTabChange }: SidebarProps) {
  return (
    <>
      {mobileOpen ? (
        <div aria-hidden="true" className="fixed inset-0 z-40 bg-black/50 md:hidden" onClick={onClose} />
      ) : null}

      <aside
        className={`fixed inset-y-0 left-0 z-50 flex w-64 shrink-0 flex-col border-r bg-background transition-transform md:sticky md:top-0 md:z-auto md:h-screen md:translate-x-0 ${
          mobileOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex items-center justify-between px-5 py-4">
          <h1 className="text-xl font-semibold tracking-normal">Coloma</h1>
          <button aria-label="Close navigation" className="text-muted-foreground md:hidden" onClick={onClose} type="button">
            <X className="h-5 w-5" />
          </button>
        </div>

        <nav aria-label="Primary" className="flex flex-1 flex-col gap-5 px-3">
          {tabGroups.map((group) => (
            <div className="space-y-1" key={group.label}>
              <p className="px-3 pb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                {group.label}
              </p>
              {group.tabs.map((tab) => {
                const Icon = tab.icon;
                return (
                  <button
                    className={
                      activeTab === tab.id
                        ? "flex w-full items-center gap-2 rounded-md bg-accent px-3 py-2 text-sm font-medium text-accent-foreground"
                        : "flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm font-medium text-muted-foreground hover:bg-muted hover:text-foreground"
                    }
                    key={tab.id}
                    onClick={() => onTabChange(tab.id)}
                    type="button"
                  >
                    <Icon className="h-4 w-4" />
                    {tab.label}
                  </button>
                );
              })}
            </div>
          ))}
        </nav>
        <div className="space-y-2 p-3">
          <EndpointStatus />
          <GpuStatus />
          <SocialLinks />
        </div>
      </aside>
    </>
  );
}
