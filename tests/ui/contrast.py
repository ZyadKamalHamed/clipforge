"""Contrast as the window draws it, against WCAG AA: 4.5:1 for text, 3:1 for large text and for the parts of
a control that show its state. Each colour is painted on a canvas and read back, so oklch, color-mix and
alpha all come out as sRGB, and what shows behind is the backgrounds under the element blended from the page
up. Text over a picture or a video is left out: what is behind it changes."""

from __future__ import annotations

from playwright.sync_api import Locator, Page

COLOURS = r"""
const canvas = document.createElement("canvas");
canvas.width = canvas.height = 1;
const pen = canvas.getContext("2d", { willReadFrequently: true });
const rgba = (css) => {
  pen.clearRect(0, 0, 1, 1);
  pen.fillStyle = "#000";
  pen.fillStyle = css;
  pen.fillRect(0, 0, 1, 1);
  const [r, g, b, a] = pen.getImageData(0, 0, 1, 1).data;
  return [r, g, b, a / 255];
};
const over = (top, under) => [0, 1, 2].map((i) => top[i] * top[3] + under[i] * (1 - top[3])).concat(1);
const lum = (c) => {
  const [r, g, b] = c.slice(0, 3).map((v) => {
    v /= 255;
    return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const ratio = (a, b) => {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
};
// What shows behind el: its own background and those under it, blended from the page up, or null over a
// picture.
const behind = (el) => {
  const layers = [];
  for (let e = el; e; e = e.parentElement) {
    const s = getComputedStyle(e);
    const media = ["IMG", "VIDEO", "CANVAS", "PICTURE"].includes(e.tagName);
    if (s.backgroundImage !== "none" || media) return null;
    if (e.parentElement?.querySelector(":scope > img, :scope > video, :scope > picture")) return null;
    const c = rgba(s.backgroundColor);
    if (c[3] > 0) layers.push(c);
    if (c[3] >= 1) break;
  }
  let colour = rgba(getComputedStyle(document.documentElement).backgroundColor);
  if (colour[3] < 1) colour = over(colour, [255, 255, 255, 1]);
  for (const c of layers.reverse()) colour = over(c, colour);
  return colour;
};
const faded = (el) => {
  let o = 1;
  for (let e = el; e; e = e.parentElement) o *= Number(getComputedStyle(e).opacity);
  return o;
};
"""

# Every piece of text on the page under its bar, as "ratio text <tag class>", once each. Disabled controls
# are left out (WCAG doesn't ask it of them), and so is text too small to read on purpose (sr-only).
LOW_TEXT = (
    "() => {"
    + COLOURS
    + r"""
  const low = new Set();
  for (const el of document.querySelectorAll("body *")) {
    const texts = [...el.childNodes].filter((n) => n.nodeType === Node.TEXT_NODE);
    const own = texts.map((n) => n.textContent).join("").trim();
    const shows = !["checkbox", "radio", "range", "color", "file", "hidden"].includes(el.type);  // not "on"
    const field = ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) && el.value && shows;
    if (!own && !field) continue;
    if (el.closest(":disabled, [aria-disabled=true], option, svg")) continue;
    const box = el.getBoundingClientRect();
    if (box.width <= 1 || box.height <= 1) continue;
    const s = getComputedStyle(el);
    if (s.visibility !== "visible" || s.display === "none") continue;
    const back = behind(el);
    if (!back) continue;
    const ink = rgba(s.color);
    ink[3] *= faded(el);
    if (ink[3] === 0) continue;
    const size = parseFloat(s.fontSize);
    const large = size >= 24 || (size >= 18.66 && Number(s.fontWeight) >= 700);
    const got = ratio(over(ink, back), back);
    if (got < (large ? 3 : 4.5)) {
      const text = (own || el.value).replace(/\s+/g, " ").slice(0, 40);
      const tag = `${el.tagName.toLowerCase()} ${String(el.className).slice(0, 80)}`;
      low.add(`${got.toFixed(2)} "${text}" <${tag}>`);
    }
  }
  return [...low];
}"""
)

# A switch's knob against its track, once any transition on either has finished.
KNOB = (
    "async (input) => {"
    + COLOURS
    + r"""
  const knob = input.nextElementSibling;
  await Promise.all([input, knob].flatMap((e) => e.getAnimations()).map((a) => a.finished));
  const track = behind(input);
  return ratio(over(rgba(getComputedStyle(knob).backgroundColor), track), track);
}"""
)

# An element's own text against what shows behind it, once any transition on it has finished.
TEXT = (
    "async (el) => {"
    + COLOURS
    + r"""
  await Promise.all(el.getAnimations().map((a) => a.finished));
  const back = behind(el);
  return ratio(over(rgba(getComputedStyle(el).color), back), back);
}"""
)


def low_text(page: Page) -> list[str]:
    """The text on the page under AA's contrast."""
    return page.evaluate(LOW_TEXT)


def knob_contrast(page: Page, switch: Locator) -> float:
    """How far the switch's knob stands out from its track, with no transition half done. The app's
    content security policy blocks style tags, so this waits the transitions out instead."""
    return switch.evaluate(KNOB)


def text_contrast(element: Locator) -> float:
    """How far an element's text stands out from what is behind it, with no transition half done."""
    return element.evaluate(TEXT)
