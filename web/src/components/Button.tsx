// The window's buttons, and the same look for a link that does a button's job.

import type { ComponentProps } from "react";

export type Variant = "primary" | "secondary" | "ghost" | "danger";

const BASE =
  "inline-flex items-center justify-center gap-2 rounded-control px-3 py-1.5 text-sm font-medium " +
  "transition-colors disabled:pointer-events-none disabled:opacity-50";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent-strong text-white hover:bg-accent-deep", // darker on hover: lighter would fall under AA
  secondary: "border border-line bg-panel-2 text-fg hover:border-faint",
  ghost: "text-muted hover:bg-panel-2 hover:text-fg",
  danger: "bg-bad-strong text-white hover:bg-red-700",
};

export function buttonClass(variant: Variant = "secondary", extra = ""): string {
  return `${BASE} ${VARIANTS[variant]} ${extra}`.trim();
}

type Props = ComponentProps<"button"> & { variant?: Variant };

export function Button({ variant, className, type = "button", ...rest }: Props) {
  return <button type={type} className={buttonClass(variant, className)} {...rest} />;
}
