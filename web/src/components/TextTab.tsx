// The editor's Text tab: the hook title with the AI's other titles a click away, its accent word, up to
// three fact cards, corrections to what the captions show, and emoji. Its changes wait for the render.

import { Plus, Trash2 } from "lucide-react";
import { useId, useState } from "react";

import { CARD_MAX, CARDS_MAX, chars, type Editing, FIX_MAX, TITLE_MAX, titleWord, titleWords } from "../lib/editor.ts";
import type { FactCard } from "../types.ts";
import { Button } from "./Button.tsx";
import { Choice } from "./Choice.tsx";
import { FieldError, TextField, Toggle } from "./Field.tsx";
import { inRange } from "./Transcript.tsx";

export const TOO_MANY_CARDS = `A clip can have at most ${CARDS_MAX} fact cards.`;

const SELECT =
  "w-full rounded-control border border-line bg-panel-2 px-3 py-2 text-sm text-fg " +
  "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

const SECTION = "flex flex-col gap-3 border-t border-line pt-4 first:border-t-0 first:pt-0";

type Props = { editing: Editing; range: [number, number] };

export function TextTab({ editing, range }: Props) {
  const { detail, value, change, error } = editing;
  const title = value<string>("hook_title");
  const accent = value<string>("accent_word");
  const ai = detail.packaging_ai;
  const others = ai ? [ai.hook_title, ...ai.hook_alts].filter((t, k, all) => t && t !== title && all.indexOf(t) === k) : [];
  const othersId = useId();
  const accentError = error("edits.accent_word");
  const emojiError = error("edits.emoji");

  // A new title keeps the accent word only where the title still has it.
  function setTitle(next: string) {
    change("hook_title", next);
    change("accent_word", titleWord(accent, next));
  }

  return (
    <div className="flex flex-col gap-6">
      <section className={SECTION}>
        <TextField
          label="Hook title"
          value={title}
          onChange={setTitle}
          count={{ n: chars(title), max: TITLE_MAX, id: "hook_title" }}
          error={error("edits.hook_title")}
        />
        {others.length > 0 && (
          <div role="group" aria-labelledby={othersId} className="flex flex-col gap-1.5">
            <span id={othersId} className="text-xs font-medium text-muted">
              Other titles
            </span>
            <div className="flex flex-wrap gap-1.5">
              {others.map((other) => (
                <button
                  key={other}
                  type="button"
                  dir="auto"
                  onClick={() => setTitle(other)}
                  className={
                    "rounded-control border border-line px-2.5 py-1 text-left text-sm text-muted hover:border-faint " +
                    "hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                  }
                >
                  {other}
                </button>
              ))}
            </div>
          </div>
        )}
        <Choice
          legend="Accent word"
          name="accent_word"
          value={accent}
          options={[{ value: "", label: "None" }, ...titleWords(title)]}
          onChange={(word) => change("accent_word", word)}
        />
        {accentError && <FieldError text={accentError} />}
      </section>
      <FactCards editing={editing} range={range} />
      <Corrections editing={editing} range={range} />
      <section className={SECTION}>
        <Toggle
          label="Emoji"
          hint="A few emoji beside the captions where the AI thinks they help."
          checked={value<boolean>("emoji")}
          onChange={(on) => change("emoji", on)}
        />
        {emojiError && <FieldError text={emojiError} />}
      </section>
    </div>
  );
}

function FactCards({ editing, range }: Props) {
  const { detail, transcript, value, change, error, errorUnder } = editing;
  const cards = value<FactCard[]>("fact_cards");
  // The sentences the clip says where it's cut now, as the engine checks a card's sentence.
  const sids = transcript
    ? [...new Set(transcript.words.filter((w) => w.sid && inRange(w, range)).map((w) => w.sid))]
    : detail.sentence_ids;
  const full = cards.length >= CARDS_MAX;

  // A sentence as its first few words, so the list reads as the clip does.
  function sentenceLabel(sid: string): string {
    const words = (transcript?.words ?? []).filter((w) => w.sid === sid).map((w) => w.text);
    if (!words.length) return sid;
    const text = words.slice(0, 8).join(" ");
    return words.length > 8 ? `${text}...` : text;
  }

  function setCard(k: number, card: FactCard) {
    change(
      "fact_cards",
      cards.map((c, n) => (n === k ? card : c)),
    );
  }

  const whole = error("edits.fact_cards"); // about the list, not one card
  return (
    <section className={SECTION}>
      <div className="flex flex-col gap-0.5">
        <h2 className="text-sm font-semibold">Fact cards</h2>
        <p className="text-xs text-faint">A short line of text shown while its sentence is said.</p>
      </div>
      {cards.map((card, k) => {
        const options = sids.includes(card.sid) ? sids : [card.sid, ...sids];
        const cardError = errorUnder(`edits.fact_cards.${k}`);
        return (
          // A card is its place in the list.
          <fieldset key={k} className="flex flex-col gap-2 rounded-card border border-line p-3">
            <legend className="px-1 text-xs font-medium text-muted">Fact card {k + 1}</legend>
            <SentencePick
              value={card.sid}
              options={options.map((sid) => ({ value: sid, label: sentenceLabel(sid) }))}
              onChange={(sid) => setCard(k, { ...card, sid })}
            />
            <TextField
              label="Text"
              value={card.text}
              onChange={(text) => setCard(k, { ...card, text })}
              count={{ n: chars(card.text), max: CARD_MAX, id: `fact_cards.${k}` }}
              error={cardError}
            />
            <div>
              <Button
                variant="ghost"
                aria-label={`Remove fact card ${k + 1}`}
                onClick={() =>
                  change(
                    "fact_cards",
                    cards.filter((_, n) => n !== k),
                  )
                }
              >
                <Trash2 aria-hidden size={14} />
                Remove
              </Button>
            </div>
          </fieldset>
        );
      })}
      {whole && <FieldError text={whole} />}
      <div className="flex flex-wrap items-center gap-3">
        <Button
          disabled={full || !sids.length}
          onClick={() => change("fact_cards", [...cards, { sid: sids[0] ?? "", text: "" }])}
        >
          <Plus aria-hidden size={14} />
          Add fact card
        </Button>
        {full && <p className="text-xs text-faint">{TOO_MANY_CARDS}</p>}
      </div>
    </section>
  );
}

function SentencePick(props: { value: string; options: { value: string; label: string }[]; onChange: (v: string) => void }) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs font-medium text-muted">
        Sentence
      </label>
      <select id={id} value={props.value} onChange={(e) => props.onChange(e.target.value)} className={SELECT}>
        {props.options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}

function Corrections({ editing, range }: Props) {
  const { transcript, value, change, errorUnder } = editing;
  const fixes = value<Record<string, string>>("word_fixes");
  const words = (transcript?.words ?? []).filter((w) => inRange(w, range));
  const textOf = (i: string) => transcript?.words.find((w) => String(w.i) === i)?.text ?? `word ${i}`;
  const [word, setWord] = useState("");
  const [shown, setShown] = useState("");
  const pickId = useId();
  const legendId = useId();
  const problem = errorUnder("edits.word_fixes");

  function add() {
    const text = shown.trim();
    if (!word || !text) return;
    change("word_fixes", { ...fixes, [word]: text });
    setWord("");
    setShown("");
  }

  function remove(i: string) {
    const rest = { ...fixes };
    delete rest[i];
    change("word_fixes", rest);
  }

  return (
    <section className={SECTION}>
      <fieldset className="flex min-w-0 flex-col gap-3" aria-describedby={legendId}>
      <legend className="mb-1 text-sm font-semibold">Caption corrections</legend>
      <p id={legendId} className="text-xs text-faint">
        Change how a word is spelled in the captions. What was said stays the same.
      </p>
      <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] items-end gap-2">
        <div className="flex flex-col gap-1">
          <label htmlFor={pickId} className="text-xs font-medium text-muted">
            Word
          </label>
          <select id={pickId} value={word} onChange={(e) => setWord(e.target.value)} className={SELECT}>
            <option value="">Choose a word</option>
            {words.map((w) => (
              <option key={w.i} value={String(w.i)}>
                {w.text}
              </option>
            ))}
          </select>
        </div>
        <TextField label="Shows as" value={shown} onChange={setShown} maxLength={FIX_MAX} />
        <Button disabled={!word || !shown.trim()} onClick={add}>
          Add correction
        </Button>
      </div>
      {Object.keys(fixes).length > 0 && (
        <ul className="flex flex-col gap-1">
          {Object.entries(fixes).map(([i, fix]) => (
            <li key={i} className="flex items-center justify-between gap-3 rounded-control bg-panel-2 px-3 py-1.5 text-sm">
              <span dir="auto">{`${textOf(i)} shows as ${fix}`}</span>
              <Button variant="ghost" aria-label={`Remove the correction to ${textOf(i)}`} onClick={() => remove(i)}>
                Remove
              </Button>
            </li>
          ))}
        </ul>
      )}
      {problem && <FieldError text={problem} />}
      </fieldset>
    </section>
  );
}
