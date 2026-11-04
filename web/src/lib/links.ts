// Whether dropped or pasted text looks like a video link, so the Create page can take it. The server
// checks every link properly; this only decides what the page offers to add.

const YOUTUBE =
  /^(?:https?:\/\/)?(?:(?:www|m|music)\.)?(?:youtube\.com\/(?:watch\?(?:[^#]*&)?v=|shorts\/|live\/|embed\/)|youtu\.be\/)[A-Za-z0-9_-]{11}(?:[?&#/]\S*)?$/;

export function looksLikeVideoLink(text: string, allowOtherSites: boolean): boolean {
  const s = text.trim();
  if (YOUTUBE.test(s)) return true;
  if (!allowOtherSites) return false;
  try {
    const url = new URL(s);
    return (url.protocol === "https:" || url.protocol === "http:") && url.hostname.includes(".");
  } catch {
    return false;
  }
}
