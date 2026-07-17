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

function DiscordIcon() {
  return (
    <svg aria-hidden="true" className="h-5 w-5" fill="currentColor" viewBox="0 0 24 24">
      <path d="M20.317 4.37a19.8 19.8 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.445.865-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.618-1.25.077.077 0 0 0-.078-.037A19.74 19.74 0 0 0 3.677 4.37a.07.07 0 0 0-.032.028C.533 9.046-.319 13.58.1 18.058a.082.082 0 0 0 .031.056c2.053 1.508 4.041 2.423 5.993 3.03a.078.078 0 0 0 .084-.028c.462-.63.873-1.295 1.226-1.994a.076.076 0 0 0-.042-.106 12.3 12.3 0 0 1-1.872-.892.077.077 0 0 1-.008-.128c.126-.094.252-.192.372-.291a.074.074 0 0 1 .078-.01c3.928 1.793 8.18 1.793 12.061 0a.074.074 0 0 1 .079.009c.12.099.246.198.373.292a.077.077 0 0 1-.007.128c-.597.342-1.22.644-1.873.891a.077.077 0 0 0-.04.107c.36.698.771 1.363 1.224 1.993a.076.076 0 0 0 .085.029c1.96-.607 3.949-1.522 6.002-3.03a.077.077 0 0 0 .031-.055c.5-5.177-.838-9.674-3.548-13.66a.061.061 0 0 0-.031-.029ZM8.02 15.331c-1.183 0-2.157-1.086-2.157-2.419s.956-2.419 2.157-2.419c1.21 0 2.176 1.095 2.157 2.419 0 1.333-.956 2.419-2.157 2.419Zm7.975 0c-1.183 0-2.157-1.086-2.157-2.419s.955-2.419 2.157-2.419c1.21 0 2.176 1.095 2.157 2.419 0 1.333-.946 2.419-2.157 2.419Z" />
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
        aria-label="Join the Discord community"
        className="rounded-md p-1.5 hover:bg-muted hover:text-foreground"
        href="https://discord.gg/E7b48D9d26"
        rel="noreferrer"
        target="_blank"
      >
        <DiscordIcon />
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
