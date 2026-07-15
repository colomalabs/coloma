import { Activity, Gauge, GitCompare, MessageSquare, Rocket, Settings, type LucideIcon } from "lucide-react";
import type { ComponentType } from "react";
import { ChatTab } from "./components/ChatTab";
import { CompareTab } from "./components/CompareTab";
import { ConfigTab } from "./components/ConfigTab";
import { DeployTab } from "./components/DeployTab";
import { DeploymentSettingsTab } from "./components/DeploymentSettingsTab";
import { PressureTestTab } from "./components/PressureTestTab";
import { TrafficTab } from "./components/TrafficTab";

type TabDefinition = {
  id: string;
  label: string;
  icon: LucideIcon;
  component: ComponentType;
};

export const tabGroups = [
  {
    label: "Watch",
    tabs: [
      { id: "chat", label: "Chat", icon: MessageSquare, component: ChatTab },
      { id: "traffic", label: "Traffic", icon: Activity, component: TrafficTab },
      { id: "config", label: "Proxy settings", icon: Settings, component: ConfigTab },
    ],
  },
  {
    label: "Deploy",
    tabs: [
      { id: "deploy", label: "Profile & deploy", icon: Rocket, component: DeployTab },
      { id: "compare", label: "Compare", icon: GitCompare, component: CompareTab },
      { id: "pressure", label: "Pressure test", icon: Gauge, component: PressureTestTab },
      {
        id: "deployment-settings",
        label: "Deployment settings",
        icon: Settings,
        component: DeploymentSettingsTab,
      },
    ],
  },
] as const satisfies ReadonlyArray<{ label: string; tabs: ReadonlyArray<TabDefinition> }>;

export type TabId = (typeof tabGroups)[number]["tabs"][number]["id"];
type Tab = (typeof tabGroups)[number]["tabs"][number];

export const tabs = tabGroups.reduce<Tab[]>((all, group) => {
  all.push(...group.tabs);
  return all;
}, []);
export const DEFAULT_TAB: TabId = tabGroups[0].tabs[0].id;
