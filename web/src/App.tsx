// The window: the rail, the header and the page, with the drop target, the toasts and the event stream
// around them all.

import { QueryClientProvider, useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import {
  createBrowserRouter,
  createRoutesFromElements,
  Outlet,
  Route,
  RouterProvider,
} from "react-router";

import { ConfirmProvider } from "./components/Confirm.tsx";
import { Header } from "./components/Header.tsx";
import { PausedBanner } from "./components/PausePosting.tsx";
import { Rail } from "./components/Rail.tsx";
import { ToastProvider } from "./components/Toasts.tsx";
import { WindowDrop } from "./components/WindowDrop.tsx";
import { connectEvents } from "./events.ts";
import { setTimeZone } from "./lib/plan.ts";
import { Accounts } from "./pages/Accounts.tsx";
import { Campaign } from "./pages/Campaign.tsx";
import { Campaigns } from "./pages/Campaigns.tsx";
import { Channels } from "./pages/Channels.tsx";
import { Create } from "./pages/Create.tsx";
import { Editor } from "./pages/Editor.tsx";
import { Library } from "./pages/Library.tsx";
import { Money } from "./pages/Money.tsx";
import { NotFound } from "./pages/NotFound.tsx";
import { Project } from "./pages/Project.tsx";
import { Projects } from "./pages/Projects.tsx";
import { Queue } from "./pages/Queue.tsx";
import { Settings } from "./pages/Settings.tsx";
import { Today } from "./pages/Today.tsx";
import { Trends } from "./pages/Trends.tsx";
import { live, queryClient, settingsQuery } from "./state.ts";

// A data router, so a page can hold the window back while it has work that isn't saved (the editor).
const router = createBrowserRouter(
  createRoutesFromElements(
    <Route element={<Shell />}>
      <Route index element={<Today />} />
      <Route path="create" element={<Create />} />
      <Route path="projects" element={<Projects />} />
      <Route path="projects/:id" element={<Project />} />
      <Route path="clips/:id" element={<Editor />} />
      <Route path="queue" element={<Queue />} />
      <Route path="channels" element={<Channels />} />
      <Route path="campaigns" element={<Campaigns />} />
      <Route path="money" element={<Money />} />
      <Route path="campaigns/:id" element={<Campaign />} />
      <Route path="accounts" element={<Accounts />} />
      <Route path="library" element={<Library />} />
      <Route path="trends" element={<Trends />} />
      <Route path="settings" element={<Settings />} />
      <Route path="*" element={<NotFound />} />
    </Route>,
  ),
);

export function App() {
  useEffect(
    () =>
      connectEvents(queryClient, (job) => live.push(job, Date.now() / 1000)),
    [],
  );
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <ConfirmProvider>
          <RouterProvider router={router} />
        </ConfirmProvider>
      </ToastProvider>
    </QueryClientProvider>
  );
}

function Shell() {
  // A drop needs the last options and the pages show every time in the posting time zone, so the settings
  // are here before the first page.
  const settings = useQuery(settingsQuery);
  if (settings.data) setTimeZone(settings.data.values.posting.timezone);
  const ready = settings.data !== undefined || settings.isError;
  return (
    <WindowDrop>
      <div className="flex h-full">
        <Rail />
        <div className="flex min-w-0 flex-1 flex-col">
          <Header />
          <PausedBanner />
          <main aria-busy={!ready} className="min-h-0 flex-1 overflow-y-auto px-8 py-6">
            {ready && <Outlet />}
          </main>
        </div>
      </div>
    </WindowDrop>
  );
}
