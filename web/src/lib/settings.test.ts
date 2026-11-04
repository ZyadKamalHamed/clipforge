import assert from "node:assert/strict";
import { test } from "node:test";

import {
  channelLines,
  marketplaceRows,
  modelsWith,
  NICHE_LINE,
  nicheLines,
  numberOrText,
  parseChannels,
  parseNiches,
  parseRates,
  RATE_LINE,
  rateLines,
  skipLine,
} from "./settings.ts";

test("parseChannels takes one channel a line, trimmed, with blank lines dropped", () => {
  assert.deepEqual(parseChannels("@me\n\n  @alsome  "), ["@me", "@alsome"]);
  assert.deepEqual(parseChannels("@a\r\n@b\r\n"), ["@a", "@b"]);
  assert.deepEqual(parseChannels("   \n"), []);
  assert.equal(channelLines(["@me", "@alsome"]), "@me\n@alsome");
  assert.equal(channelLines(parseChannels(" @x \n\n")), "@x");
});

test("numberOrText sends a number when the draft is one, else the text for the engine to refuse", () => {
  assert.equal(numberOrText("5"), 5);
  assert.equal(numberOrText(" 2.5 "), 2.5);
  assert.equal(numberOrText("0"), 0);
  assert.equal(numberOrText("five"), "five");
  assert.equal(numberOrText(""), "");
  assert.equal(numberOrText("  "), "  ");
  assert.equal(numberOrText("Infinity"), "Infinity");
});

test("modelsWith sets one pass's model and drops it, and an emptied provider, when cleared", () => {
  assert.deepEqual(modelsWith({}, "claude_cli", "discover", " sonnet "), {
    claude_cli: { discover: "sonnet" },
  });
  const two = {
    claude_cli: { discover: "sonnet", rerank: "opus" },
    anthropic: { review: "x" },
  };
  assert.deepEqual(modelsWith(two, "claude_cli", "discover", ""), {
    claude_cli: { rerank: "opus" },
    anthropic: { review: "x" },
  });
  assert.deepEqual(
    modelsWith(
      { claude_cli: { discover: "sonnet" } },
      "claude_cli",
      "discover",
      "  ",
    ),
    {},
  );
  assert.deepEqual(two.claude_cli, { discover: "sonnet", rerank: "opus" }); // left as it was
});

test("skipLine says which uploads the clipping channels skip, in words", () => {
  assert.equal(
    skipLine({ min_minutes: 4, max_hours: 4 }),
    "Clipping channels skip videos under 4 minutes and over 4 hours.",
  );
  assert.equal(
    skipLine({ min_minutes: 1, max_hours: 1 }),
    "Clipping channels skip videos under 1 minute and over 1 hour.",
  );
  assert.equal(
    skipLine({ min_minutes: 2.5, max_hours: 0.5 }),
    "Clipping channels skip videos under 2.5 minutes and over 0.5 hours.",
  );
});

test("marketplaceRows gives each marketplace once, however it is spelt, in name order", () => {
  const saved = { vyro: { withdraw_url: null, min_payout: 10 } };
  // The saved spelling wins, else the first campaign's; one saved with no campaign left still shows.
  assert.deepEqual(
    marketplaceRows(["Whop", "Vyro", "whop", " Clipping Co "], saved),
    ["Clipping Co", "vyro", "Whop"],
  );
  assert.deepEqual(marketplaceRows([], saved), ["vyro"]);
  assert.deepEqual(marketplaceRows(["", "  "], {}), []);
});

test("parseRates reads one currency code and its US$ value a line, and rateLines writes them back", () => {
  assert.deepEqual(parseRates("aud 0.65\n\n  EUR   1.08 "), {
    rates: { AUD: 0.65, EUR: 1.08 },
  });
  assert.deepEqual(parseRates("AUD=0.65\nGBP: 1.27"), {
    rates: { AUD: 0.65, GBP: 1.27 },
  });
  assert.deepEqual(parseRates(""), { rates: {} });
  for (const wrong of [
    "AUD",
    "AUD x",
    "AU 0.65",
    "AUDD 0.65",
    "AUD 0",
    "AUD -1",
    "AUD 0.65 extra",
  ]) {
    assert.deepEqual(parseRates(wrong), { error: RATE_LINE }, wrong);
  }
  assert.ok(RATE_LINE.endsWith(".") && RATE_LINE.includes("AUD 0.65"));
  assert.equal(rateLines({ AUD: 0.65, EUR: 1.08 }), "AUD 0.65\nEUR 1.08");
  assert.equal(rateLines({}), "");
});

test("parseNiches reads a niche a line, its language in brackets, its tags after the colon", () => {
  assert.deepEqual(
    parseNiches(
      "Gaming (en): Gaming, games ,GAMES,  video   games\n\n  Anime: anime, manga  ",
    ),
    {
      niches: [
        {
          name: "Gaming",
          language: "en",
          tags: ["gaming", "games", "video games"],
        },
        { name: "Anime", language: "en", tags: ["anime", "manga"] },
      ],
    },
  );
  assert.deepEqual(parseNiches("Fútbol (ES): fútbol"), {
    niches: [{ name: "Fútbol", language: "es", tags: ["fútbol"] }],
  });
  for (const wrong of [
    "no tags here",
    "Gaming:",
    "Gaming (en): , ,",
    ": gaming",
    "(en): gaming",
  ]) {
    assert.deepEqual(parseNiches(wrong), { error: NICHE_LINE }, wrong);
  }
  assert.deepEqual(parseNiches("  \n"), {
    error: `Add at least one niche. ${NICHE_LINE}`,
  });
  const many = Array.from({ length: 31 }, (_, i) => `N${i}: t`).join("\n");
  assert.deepEqual(parseNiches(many), {
    error: "Keep it to 30 niches or fewer.",
  });
  assert.deepEqual(parseNiches("Gaming: a\ngaming: b"), {
    error: "Two niches can't share a name.",
  });
  assert.equal(
    NICHE_LINE,
    "Write each niche like Gaming (en): gaming, games, one a line.",
  );
});

test("nicheLines writes the niches back one a line, as parseNiches reads them", () => {
  const niches = [
    {
      name: "Reality TV and drama",
      language: "en",
      tags: ["reality", "drama"],
    },
    { name: "Fútbol", language: "es", tags: ["fútbol"] },
  ];
  const text = nicheLines(niches);
  assert.equal(
    text,
    "Reality TV and drama (en): reality, drama\nFútbol (es): fútbol",
  );
  assert.deepEqual(parseNiches(text), { niches });
  assert.equal(nicheLines([]), "");
});
