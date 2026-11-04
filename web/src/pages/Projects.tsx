// The Projects page (SPEC 14.2): every project, newest first, with filter chips and a title search.

import { useQuery } from "@tanstack/react-query";
import { FolderOpen, Search, SearchX } from "lucide-react";
import { useState } from "react";

import { api, key, sentence } from "../api.ts";
import { Button } from "../components/Button.tsx";
import { Choice } from "../components/Choice.tsx";
import { Empty } from "../components/Empty.tsx";
import { ProjectCard } from "../components/ProjectCard.tsx";
import {
  FILTERS,
  type Filter,
  inFilter,
  matchesSearch,
} from "../lib/projects.ts";
import type { Project } from "../types.ts";

const MOST = 500; // the most the engine lists at once

export function Projects() {
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");
  const list = useQuery({
    queryKey: [...key.projects, "all"],
    queryFn: ({ signal }) =>
      api<{ projects: Project[] }>(`/api/projects?limit=${MOST}`, { signal }),
  });
  const all = list.data?.projects ?? [];
  const shown = all.filter(
    (p) => inFilter(p, filter) && matchesSearch(p, search),
  );
  const chips = FILTERS.map((f) => ({
    ...f,
    hint: String(all.filter((p) => inFilter(p, f.value)).length),
  }));

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-5">
      <h1 className="text-xl font-semibold">Projects</h1>
      {list.isError && !list.data ? (
        <p className="text-sm text-muted">{sentence(list.error)}</p>
      ) : list.isPending ? null : all.length === 0 ? (
        <Empty icon={FolderOpen} title="No projects yet.">
          Drop a YouTube or Drive link anywhere to start.
        </Empty>
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <Choice
              legend="Show"
              legendHidden
              name="show"
              value={filter}
              options={chips}
              onChange={setFilter}
            />
            <div className="relative w-full max-w-xs">
              <Search
                aria-hidden
                size={16}
                className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-faint"
              />
              <input
                type="search"
                aria-label="Search projects"
                placeholder="Search by title"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className={
                  "w-full rounded-control border border-line bg-bg py-1.5 pr-3 pl-9 text-sm text-fg " +
                  "placeholder:text-faint hover:border-faint focus-visible:border-accent"
                }
              />
            </div>
          </div>
          {shown.length === 0 ? (
            <Empty
              icon={SearchX}
              title="No projects match."
              action={
                <Button
                  onClick={() => {
                    setFilter("all");
                    setSearch("");
                  }}
                >
                  Show all
                </Button>
              }
            />
          ) : (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(15rem,1fr))] gap-4">
              {shown.map((project) => (
                <ProjectCard key={project.id} project={project} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
