"use client";

import { useState } from "react";
import { Bookmark, ChevronDown, Save, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import { useSavedViewsQuery, type QueueFilter } from "@/lib/queries/reviewer";
import { useDeleteView, useSaveView } from "@/lib/mutations/reviewer";

/**
 * SavedViewsBar — dropdown of saved queue filter sets + a "Save current view"
 * dialog that snapshots the current filter into a new saved view.
 *
 * The current view is described by the `current` prop so consumers can
 * persist whatever subset of the URL they care about (filter chip,
 * supplier, agent search, etc.). The save endpoint accepts any JSON object
 * for `filters`, so adding new dimensions later doesn't require a schema
 * change.
 */
export function SavedViewsBar({
  current,
  onApply,
}: {
  current: { filter: QueueFilter; q?: string };
  onApply: (filters: { filter?: QueueFilter; q?: string }) => void;
}) {
  const views = useSavedViewsQuery();
  const save = useSaveView();
  const del = useDeleteView();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [name, setName] = useState("");

  const items = (views.data?.views ?? []).filter((v) => v.endpoint === "/api/queue");

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger render={<Button variant="outline" size="sm" />}>
          <Bookmark className="mr-2 h-3.5 w-3.5" />
          Saved views
          <ChevronDown className="ml-1 h-3 w-3 opacity-60" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="min-w-[220px]">
          {/* Base UI Menu.Label requires a Menu.Group parent
              (DropdownMenuGroup). Without it, useMenuGroupRootContext()
              throws Base UI error #31 ("MenuGroupRootContext is missing")
              the moment the menu mounts. */}
          <DropdownMenuGroup>
            <DropdownMenuLabel>Your views</DropdownMenuLabel>
            {items.length === 0 ? (
              <DropdownMenuItem disabled>No saved views yet</DropdownMenuItem>
            ) : (
              items.map((v) => (
                <DropdownMenuItem
                  key={v.id}
                  onSelect={() =>
                    onApply({
                      filter: (v.filters?.filter as QueueFilter | undefined) ?? "all",
                      q: (v.filters?.q as string | undefined) ?? "",
                    })
                  }
                  className="flex items-center justify-between gap-2"
                >
                  <span className="flex-1 truncate">{v.name}</span>
                  <button
                    type="button"
                    aria-label={`Delete saved view ${v.name}`}
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      del.mutate(v.id);
                    }}
                    className="rounded p-0.5 text-[var(--text-muted)] opacity-60 transition-opacity hover:bg-red-500/10 hover:text-red-400 hover:opacity-100"
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                </DropdownMenuItem>
              ))
            )}
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => setDialogOpen(true)}>
            <Save className="mr-2 h-3.5 w-3.5" />
            Save current view
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-[420px]">
          <DialogHeader>
            <DialogTitle>Save current view</DialogTitle>
          </DialogHeader>
          <div className="grid gap-3 py-2">
            <div className="grid gap-2">
              <Label htmlFor="view-name">View name</Label>
              <Input
                id="view-name"
                placeholder="e.g. High-priority backlog"
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoFocus
              />
            </div>
            <div className="rounded-md border border-[var(--border-subtle)] bg-[var(--bg-elev1)] p-3 text-[12px] text-[var(--text-muted)]">
              Filter:{" "}
              <span className="font-mono text-[var(--text-primary)]">{current.filter}</span>
              {current.q ? (
                <>
                  {" · "}
                  Search: <span className="font-mono text-[var(--text-primary)]">{current.q}</span>
                </>
              ) : null}
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              Cancel
            </Button>
            <Button
              disabled={!name.trim() || save.isPending}
              onClick={async () => {
                await save.mutateAsync({
                  name: name.trim(),
                  endpoint: "/api/queue",
                  filters: { filter: current.filter, q: current.q ?? "" },
                });
                setName("");
                setDialogOpen(false);
              }}
            >
              {save.isPending ? "Saving…" : "Save view"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
