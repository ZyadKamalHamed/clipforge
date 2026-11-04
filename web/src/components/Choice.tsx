// A few choices as pills, one of them picked. They are radio buttons underneath, each laid over its pill,
// so the arrow keys move between them and a screen reader says them as one named group.

type Option<T extends string> = { value: T; label: string; hint?: string };

type Props<T extends string> = {
  legend: string;
  name: string;
  value: T;
  options: Option<T>[];
  onChange: (value: T) => void;
  legendHidden?: boolean; // when what sits around the group already says what it is
};

export function Choice<T extends string>({ legend, name, value, options, onChange, legendHidden }: Props<T>) {
  return (
    <fieldset className="min-w-0">
      <legend className={legendHidden ? "sr-only" : "mb-1.5 text-xs font-medium text-muted"}>{legend}</legend>
      <div className="flex flex-wrap gap-1.5">
        {options.map((option) => (
          <label
            key={option.value}
            className={
              "relative inline-flex cursor-pointer items-center gap-1 rounded-full border border-line px-3 py-1 " +
              "text-sm text-muted hover:border-faint hover:text-fg has-[:checked]:border-accent-strong " +
              "has-[:checked]:bg-accent-strong has-[:checked]:text-white has-[:focus-visible]:outline-2 " +
              "has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-accent"
            }
          >
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={option.value === value}
              onChange={() => onChange(option.value)}
              className="absolute inset-0 z-10 cursor-pointer appearance-none rounded-full opacity-0"
            />
            {option.label}
            {option.hint && <span className="text-xs">{option.hint}</span>}
          </label>
        ))}
      </div>
    </fieldset>
  );
}
