// A labelled text field with its counter, a mark beside its label ("Check this") and the engine's sentence
// when it refused the value, a date and time field, and a switch.
// Untrusted text (titles, captions) is only ever a field's value, shown as text.

import { useId } from "react";

const FIELD =
  "w-full rounded-control border border-line bg-panel-2 px-3 py-2 text-sm text-fg placeholder:text-faint " +
  "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent aria-[invalid=true]:border-bad";

type FieldProps = {
  label: string;
  value: string;
  onChange: (value: string) => void;
  onBlur?: () => void;
  count?: { n: number; max: number; id: string }; // shown as "{n} of {max}", over the max in the bad colour
  error?: string | null;
  multiline?: boolean;
  maxLength?: number;
  hint?: string;
  warning?: string | null; // what the value risks, said beside it in the warning colour; it still saves
  placeholder?: string;
  onEnter?: () => void; // Enter in a one-line field
  inputMode?: "text" | "numeric" | "decimal";
  secret?: boolean; // a pasted key: shown as dots, never offered for saving, spell checked or capitalised
  mark?: string | null; // said beside the label in the warning colour, as a field Claude filled for you to check
  date?: boolean; // a day, as "2026-11-30"
};

export function TextField(props: FieldProps) {
  const { label, value, onChange, onBlur, count, error, multiline, maxLength, hint, warning, placeholder, onEnter, inputMode, secret } =
    props;
  const { mark, date } = props;
  const id = useId();
  const described = [
    mark && `${id}-mark`,
    count && `${id}-count`,
    error && `${id}-error`,
    hint && `${id}-hint`,
    warning && `${id}-warning`,
  ]
    .filter(Boolean)
    .join(" ");
  const shared = {
    id,
    value,
    dir: "auto",
    maxLength,
    placeholder,
    onBlur,
    "aria-invalid": error ? true : undefined,
    "aria-describedby": described || undefined,
    className: date ? `${FIELD} [color-scheme:light_dark]` : FIELD,
  } as const;
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between gap-2">
        <span className="flex items-baseline gap-2">
          <label htmlFor={id} className="text-xs font-medium text-muted">
            {label}
          </label>
          {mark && (
            <span id={`${id}-mark`} className="text-xs font-medium text-warn">
              {mark}
            </span>
          )}
        </span>
        {count && (
          <span
            id={`${id}-count`}
            data-testid={`${count.id}-count`}
            className={`text-xs tabular-nums ${count.n > count.max ? "text-bad" : "text-faint"}`}
          >
            {count.n} of {count.max}
          </span>
        )}
      </div>
      {multiline ? (
        <textarea {...shared} rows={4} onChange={(e) => onChange(e.target.value)} />
      ) : (
        <input
          {...shared}
          type={secret ? "password" : date ? "date" : "text"}
          {...(secret ? { autoComplete: "off", spellCheck: false, autoCapitalize: "off" } : {})}
          inputMode={inputMode}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && onEnter && !e.nativeEvent.isComposing) onEnter();
          }}
        />
      )}
      {hint && (
        <p id={`${id}-hint`} className="text-xs text-faint">
          {hint}
        </p>
      )}
      {warning && (
        <p id={`${id}-warning`} role="status" className="text-xs text-warn">
          {warning}
        </p>
      )}
      {error && <FieldError id={`${id}-error`} text={error} />}
    </div>
  );
}

export function FieldError({ id, text }: { id?: string; text: string }) {
  return (
    <p id={id} role="alert" className="text-xs text-bad">
      {text}
    </p>
  );
}

type ToggleProps = {
  label: string;
  checked: boolean;
  onChange: (on: boolean) => void;
  hint?: string;
  disabled?: boolean;
};

// A switch: a checkbox underneath, so Space flips it and a screen reader says it's on or off.
export function Toggle({ label, checked, onChange, hint, disabled = false }: ToggleProps) {
  const id = useId();
  return (
    <div className="flex items-start justify-between gap-4 py-1.5">
      <label htmlFor={id} className={`flex flex-col text-sm${disabled ? " text-muted" : ""}`}>
        {label}
        {hint && <span className="text-xs text-faint">{hint}</span>}
      </label>
      <span className="relative inline-flex shrink-0">
        <input
          id={id}
          type="checkbox"
          role="switch"
          checked={checked}
          disabled={disabled}
          onChange={(e) => onChange(e.target.checked)}
          className={
            "peer h-5 w-9 cursor-pointer appearance-none rounded-full bg-line transition-colors checked:bg-accent-strong " +
            "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent " +
            "disabled:cursor-not-allowed disabled:opacity-50"
          }
        />
        <span
          aria-hidden
          className="pointer-events-none absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-faint transition peer-checked:translate-x-4 peer-checked:bg-white"
        />
      </span>
    </div>
  );
}

export const TIME_FIELD =
  "rounded-control border border-line bg-panel-2 px-2.5 py-1.5 text-sm text-fg [color-scheme:light_dark] " +
  "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent aria-[invalid=true]:border-bad";

type DateTimeProps = { label: string; value: string; onChange: (text: string) => void; error?: string };

// A date and time in the posting time zone, as "2026-11-03T19:40".
export function DateTimeField({ label, value, onChange, error }: DateTimeProps) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs font-medium text-muted">
        {label}
      </label>
      <input
        id={id}
        type="datetime-local"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : undefined}
        className={TIME_FIELD}
      />
      {error && <FieldError id={`${id}-error`} text={error} />}
    </div>
  );
}
