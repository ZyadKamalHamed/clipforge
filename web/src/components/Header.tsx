// The bar along the top of the window (SPEC 14.1): the Pause posting switch and the jobs indicator.

import { JobsIndicator } from "./JobsIndicator.tsx";
import { PausePosting } from "./PausePosting.tsx";

export function Header() {
  return (
    <header className="flex h-14 shrink-0 items-center justify-end gap-5 border-b border-line px-6">
      <PausePosting />
      <JobsIndicator />
    </header>
  );
}
