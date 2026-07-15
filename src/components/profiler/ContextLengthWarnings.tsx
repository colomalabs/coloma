import type { ContextLengthWarning } from "../../types";
import { WarningNotice } from "./WarningNotice";

export function ContextLengthWarnings({ warnings }: { warnings: ContextLengthWarning[] }) {
  // The backend words every capacity failure it reports; this only renders what it sent.
  return warnings.map((warning, index) => (
    <WarningNotice key={`${warning.max_model_len}-${index}`}>{warning.message}</WarningNotice>
  ));
}
