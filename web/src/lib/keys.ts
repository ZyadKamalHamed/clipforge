// The keyboard shortcuts (SPEC 14.8) and whether a key press is one of them. A shortcut never fires while
// a text field has the focus, where Space types a space and Delete deletes, except Esc and Cmd+Enter,
// which type nothing. Keys pressed inside a dialog belong to the dialog.

export type Binding = { keys: readonly string[]; cmd?: boolean; inFields?: boolean };

type Target = {
  tagName?: string;
  type?: string;
  isContentEditable?: boolean;
  getAttribute?: (name: string) => string | null;
  closest?: (selector: string) => unknown;
};

export type KeyPress = {
  key: string;
  metaKey: boolean;
  ctrlKey: boolean;
  altKey: boolean;
  shiftKey: boolean;
  isComposing?: boolean;
  defaultPrevented?: boolean;
  target?: Target | EventTarget | null;
};

export const KEYS = {
  playPause: { keys: [" "] },
  back: { keys: ["j"] },
  forward: { keys: ["l"] },
  frameBack: { keys: [","] },
  frameForward: { keys: ["."] },
  setIn: { keys: ["i"] },
  setOut: { keys: ["o"] },
  cut: { keys: ["Delete", "Backspace"] }, // a Mac's delete key reaches the browser as Backspace
  rerender: { keys: ["Enter"], cmd: true, inFields: true },
  selectAll: { keys: ["a"], cmd: true },
  post: { keys: ["p"] },
  close: { keys: ["Escape"], inFields: true },
} as const satisfies Record<string, Binding>;

// Inputs that take no typing: Space presses them instead.
const UNTYPED = new Set(["checkbox", "radio", "range", "button", "submit", "reset", "color", "file", "image"]);
const PRESSED_BY_SPACE = new Set(["button", "checkbox", "switch", "radio", "tab", "menuitem", "option"]);

// Whether the focus is where a key types something (so a paste there is the field's own).
export function inTextField(target: Target | EventTarget | null | undefined): boolean {
  const t = (target ?? {}) as Target;
  if (t.isContentEditable || t.tagName === "TEXTAREA" || t.tagName === "SELECT") return true;
  return t.tagName === "INPUT" && !UNTYPED.has((t.type ?? "text").toLowerCase());
}

function pressedBySpace(t: Target): boolean {
  if (t.tagName === "BUTTON" || t.tagName === "SUMMARY" || t.tagName === "INPUT") return true;
  return PRESSED_BY_SPACE.has(t.getAttribute?.("role") ?? "");
}

export function matchKey(e: KeyPress, binding: Binding): boolean {
  if (e.defaultPrevented || e.isComposing || e.ctrlKey || e.altKey || e.shiftKey) return false;
  if (e.metaKey !== (binding.cmd ?? false)) return false;
  const key = e.key.toLowerCase();
  if (!binding.keys.some((k) => k.toLowerCase() === key)) return false;
  const t = (e.target ?? {}) as Target;
  if (t.closest?.('[role="dialog"], [role="alertdialog"]')) return false;
  if (!binding.inFields && inTextField(t)) return false;
  return !(key === " " && pressedBySpace(t));
}
