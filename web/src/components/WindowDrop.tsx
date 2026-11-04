// The whole window takes drops and pastes (SPEC 14.1). While something droppable is dragged over it, an
// overlay says what letting go will do, unless the page has a drop zone of its own on screen, which says it
// there instead. A dropped or pasted link starts a project with the Create page's last options, and so
// does a dropped video file, uploaded with its progress in a toast. One new project opens; several open
// Projects. Post as it is is the one saved option that changes what comes out without the switch being on
// screen, so a toast says when it was on. A paste into a text field is the field's own, and a link this
// window copied never starts one.

import { useQueryClient } from "@tanstack/react-query";
import { Upload } from "lucide-react";
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useEffectEvent,
  useMemo,
  useState,
} from "react";
import { useNavigate } from "react-router";

import { api, key, sentence, upload } from "../api.ts";
import {
  NOT_A_VIDEO,
  droppedText,
  hasLink,
  headerJson,
  isVideoFile,
  wasCopiedHere,
} from "../lib/drop.ts";
import { inTextField } from "../lib/keys.ts";
import { settingsQuery } from "../state.ts";
import type { Project, ProjectsAdded } from "../types.ts";
import { useToast } from "./Toasts.tsx";

const DROPPABLE = new Set(["Files", "text/uri-list", "text/plain"]);

type Drag = { dragging: boolean; claim: () => () => void };

const Context = createContext<Drag>({ dragging: false, claim: () => () => {} });

// A page's own drop zone: while it is on screen the overlay steps aside, and this says when to show the drop.
export function useDropZone(): boolean {
  const { dragging, claim } = useContext(Context);
  useEffect(claim, [claim]);
  return dragging;
}

const AS_IS_ON =
  "Added with Post as it is on, so each video is one uncut clip. To cut clips instead, turn it off in Create's Options and add it again.";

// Starting projects from links or files, with a toast for each one that can't be made. With cue, for when
// Create's switches aren't on screen, a toast also says when Post as it is was on.
export function useStart({ cue = false }: { cue?: boolean } = {}) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const toast = useToast();

  function open(made: Project[]) {
    if (made.length === 0) return;
    if (cue && made.some((p) => p.options.as_is)) toast({ text: AS_IS_ON });
    void client.invalidateQueries({ queryKey: key.projects });
    navigate(made.length === 1 ? `/projects/${made[0].id}` : "/projects");
  }

  async function links(text: string): Promise<void> {
    try {
      const { values } = await client.ensureQueryData(settingsQuery);
      const added = await api<ProjectsAdded>("/api/projects", {
        method: "POST",
        body: JSON.stringify({ text, options: values.create_options }),
      });
      const several = added.projects.length + added.rejected.length > 1;
      for (const r of added.rejected)
        toast({
          text: r.message,
          kind: "bad",
          subject: several ? r.input : undefined,
        });
      open(added.projects);
    } catch (err) {
      toast({ text: sentence(err), kind: "bad" });
    }
  }

  async function files(dropped: File[]): Promise<void> {
    const made: Project[] = [];
    for (const file of dropped) {
      if (!isVideoFile(file.name)) {
        toast({ text: NOT_A_VIDEO, kind: "bad", subject: file.name });
        continue;
      }
      const id = toast({
        text: "Uploading",
        subject: file.name,
        progress: 0,
        ms: 0,
      });
      try {
        const { values } = await client.ensureQueryData(settingsQuery);
        const headers = {
          "X-File-Name": encodeURIComponent(file.name),
          "X-Options": headerJson(values.create_options),
        };
        made.push(
          await upload<Project>(
            "/api/projects/upload",
            file,
            headers,
            (share) =>
              toast({
                id,
                text: "Uploading",
                subject: file.name,
                progress: share,
                ms: 0,
              }),
          ),
        );
        toast.dismiss(id);
      } catch (err) {
        toast({ id, text: sentence(err), kind: "bad", subject: file.name });
      }
    }
    open(made);
  }

  return { links, files };
}

export function WindowDrop({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const start = useStart({ cue: true });
  const [shown, setShown] = useState(false);
  const [zones, setZones] = useState(0);
  const claim = useCallback(() => {
    setZones((n) => n + 1);
    return () => setZones((n) => n - 1);
  }, []);
  const drag = useMemo(() => ({ dragging: shown, claim }), [shown, claim]);

  // Read while the drop is happening: its data can't be read once the event is over.
  const dropped = useEffectEvent((data: DataTransfer) => {
    const files = [...data.files];
    const text = files.length ? "" : droppedText((type) => data.getData(type));
    if (files.length) void start.files(files);
    else if (text.trim()) void start.links(text);
  });

  const pasted = useEffectEvent((e: ClipboardEvent) => {
    if (e.defaultPrevented || inTextField(e.target)) return;
    const text = e.clipboardData?.getData("text/plain") ?? "";
    const allowOtherSites =
      client.getQueryData(settingsQuery.queryKey)?.values.allow_other_sites ??
      false;
    if (wasCopiedHere(text) || !hasLink(text, allowOtherSites)) return;
    e.preventDefault();
    void start.links(text);
  });

  useEffect(() => {
    let depth = 0; // dragenter and dragleave come in pairs as the pointer crosses elements
    let ours = false; // a drag that began in this window (a selection, a link) is left to the browser
    const droppable = (e: DragEvent) =>
      !ours && [...(e.dataTransfer?.types ?? [])].some((t) => DROPPABLE.has(t));
    const hide = () => {
      depth = 0;
      setShown(false);
    };
    const onStart = () => {
      ours = true;
    };
    const onEnd = () => {
      ours = false;
      hide();
    };
    const onEnter = (e: DragEvent) => {
      if (!droppable(e)) return;
      e.preventDefault();
      depth += 1;
      setShown(true);
    };
    const onOver = (e: DragEvent) => {
      if (!droppable(e)) return;
      e.preventDefault(); // which is what lets it drop here at all
      if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
    };
    const onLeave = (e: DragEvent) => {
      if (!droppable(e)) return;
      depth -= 1;
      // Leaving the window doesn't always reach the count, so reaching its edge counts too.
      const out =
        e.clientX <= 0 ||
        e.clientY <= 0 ||
        e.clientX >= innerWidth ||
        e.clientY >= innerHeight;
      if (depth <= 0 || out) hide();
    };
    const onDrop = (e: DragEvent) => {
      if (!droppable(e)) return;
      e.preventDefault(); // or the browser opens the file or the link itself
      hide();
      if (e.dataTransfer) dropped(e.dataTransfer);
    };
    const onPaste = (e: ClipboardEvent) => pasted(e);
    const drags = {
      dragstart: onStart,
      dragend: onEnd,
      dragenter: onEnter,
      dragover: onOver,
      dragleave: onLeave,
      drop: onDrop,
    };
    for (const [type, listener] of Object.entries(drags))
      window.addEventListener(type, listener as EventListener);
    document.addEventListener("paste", onPaste);
    return () => {
      for (const [type, listener] of Object.entries(drags))
        window.removeEventListener(type, listener as EventListener);
      document.removeEventListener("paste", onPaste);
    };
  }, []);

  return (
    <Context value={drag}>
      {children}
      {shown && zones === 0 && <Overlay />}
    </Context>
  );
}

function Overlay() {
  return (
    <div className="pointer-events-none fixed inset-0 z-50 grid place-items-center bg-bg/80 p-6 backdrop-blur-sm">
      <div className="flex flex-col items-center gap-2 rounded-card border-2 border-dashed border-accent bg-panel px-12 py-10 text-center shadow-2xl">
        <Upload aria-hidden size={28} className="text-accent" />
        <p className="text-lg font-semibold">Drop to start a project</p>
        <p className="text-sm text-muted">
          A YouTube or Drive link, or a video file
        </p>
      </div>
    </div>
  );
}
