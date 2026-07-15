import type { ReactNode } from "react";
import { AlertTriangle } from "lucide-react";

export function WarningNotice({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-start gap-2 rounded-md py-3 text-sm text-amber-700 dark:text-amber-400">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
      <span>{children}</span>
    </div>
  );
}
