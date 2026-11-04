// The clips picked on a project's page: Shift extends from the last one clicked over the shown order.

export type Selection = { ids: ReadonlySet<string>; anchor: string | null };

export const empty = (): Selection => ({ ids: new Set(), anchor: null });

export function toggle(sel: Selection, id: string): Selection {
  const ids = new Set(sel.ids);
  if (!ids.delete(id)) ids.add(id);
  return { ids, anchor: id };
}

export function extendTo(sel: Selection, id: string, order: readonly string[]): Selection {
  const from = sel.anchor === null ? -1 : order.indexOf(sel.anchor);
  const to = order.indexOf(id);
  if (from < 0 || to < 0) return toggle(sel, id);
  const ids = new Set(sel.ids);
  for (const each of order.slice(Math.min(from, to), Math.max(from, to) + 1)) ids.add(each);
  return { ids, anchor: sel.anchor };
}

export const selectAll = (order: readonly string[]): Selection => ({ ids: new Set(order), anchor: null });

export function selectTier(clips: ReadonlyArray<{ id: string; tier: string | null }>, tier: string): Selection {
  return { ids: new Set(clips.filter((c) => c.tier === tier).map((c) => c.id)), anchor: null };
}
