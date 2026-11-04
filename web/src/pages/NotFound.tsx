// Any address the window has no page for.

import { Compass } from "lucide-react";
import { Link } from "react-router";

import { buttonClass } from "../components/Button.tsx";
import { Empty } from "../components/Empty.tsx";

export function NotFound() {
  const start = (
    <Link to="/" className={buttonClass("primary")}>
      Go to the start
    </Link>
  );
  return (
    <Empty icon={Compass} title="That page isn't here." level={1} action={start}>
      The link may be old, or what it showed may have been deleted.
    </Empty>
  );
}
