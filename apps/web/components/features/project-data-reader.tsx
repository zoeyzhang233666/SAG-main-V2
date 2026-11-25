"use client";

import * as React from "react";
import { useTranslations } from "next-intl";
import {
  Boxes,
  ChevronLeft,
  ChevronRight,
  FileText,
  Network,
  Search,
  Sparkles,
  Tags,
} from "lucide-react";

import { api, ApiError } from "@/lib/api";
import type {
  Doc,
  ProjectAsset,
  ProjectAssetKind,
  ProjectAssetsResponse,
  ProjectAssetSummary,
  ProjectChunkAsset,
  ProjectEntityAsset,
  ProjectEventAsset,
} from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 30;

function isChunk(item: ProjectAsset): item is ProjectChunkAsset {
  return "heading" in item;
}

function isEvent(item: ProjectAsset): item is ProjectEventAsset {
  return "title" in item;
}

function isEntity(item: ProjectAsset): item is ProjectEntityAsset {
  return "name" in item && "type" in item;
}

export function ProjectDataReader({ sourceId, documents }: { sourceId: string; documents: Doc[] }) {
  const t = useTranslations("ProjectReader");
  const [kind, setKind] = React.useState<ProjectAssetKind>("chunks");
  const [query, setQuery] = React.useState("");
  const [debouncedQuery, setDebouncedQuery] = React.useState("");
  const [documentId, setDocumentId] = React.useState("all");
  const [entityTypes, setEntityTypes] = React.useState<string[]>([]);
  const [page, setPage] = React.useState(1);
  const [summary, setSummary] = React.useState<ProjectAssetSummary | null>(null);
  const [data, setData] = React.useState<ProjectAssetsResponse | null>(null);
  const [selected, setSelected] = React.useState<ProjectAsset | null>(null);
  const [error, setError] = React.useState("");

  React.useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query.trim()), 280);
    return () => window.clearTimeout(timer);
  }, [query]);

  React.useEffect(() => {
    const controller = new AbortController();
    api.getProjectAssetSummary(sourceId, controller.signal)
      .then(setSummary)
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof ApiError ? reason.message : t("loadFailed"));
        }
      });
    return () => controller.abort();
  }, [sourceId, t]);

  React.useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError("");
    api.getProjectAssets(sourceId, {
      kind,
      q: debouncedQuery,
      documentId: documentId === "all" ? undefined : documentId,
      entityTypes: kind === "entities" ? entityTypes : undefined,
      page,
      pageSize: PAGE_SIZE,
      signal: controller.signal,
    })
      .then((value) => {
        setData(value);
        setSelected((current) => {
          if (current && value.items.some((item) => item.id === current.id)) return current;
          return value.items[0] ?? null;
        });
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof ApiError ? reason.message : t("loadFailed"));
        }
      });
    return () => controller.abort();
  }, [debouncedQuery, documentId, entityTypes, kind, page, sourceId, t]);

  const documentNames = React.useMemo(
    () => new Map(documents.map((document) => [document.id, document.filename])),
    [documents],
  );
  const pageCount = Math.max(1, Math.ceil((data?.total ?? 0) / PAGE_SIZE));
  const changeKind = (value: string) => {
    setKind(value as ProjectAssetKind);
    setPage(1);
    setSelected(null);
  };
  const toggleType = (value: string) => {
    setEntityTypes((current) =>
      current.includes(value) ? current.filter((item) => item !== value) : [...current, value],
    );
    setPage(1);
  };

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        {[
          [FileText, t("documents"), summary?.documents],
          [Boxes, t("chunks"), summary?.chunks],
          [Sparkles, t("events"), summary?.events],
          [Network, t("entities"), summary?.entities],
        ].map(([Icon, label, value]) => {
          const MetricIcon = Icon as typeof FileText;
          return (
            <div key={String(label)} className="flex items-center gap-3 rounded-xl border bg-card p-3 shadow-soft">
              <div className="grid size-9 place-items-center rounded-lg bg-primary/10 text-primary">
                <MetricIcon className="size-4" />
              </div>
              <div>
                <div className="text-lg font-semibold tabular-nums">{value == null ? "—" : String(value)}</div>
                <div className="text-[11px] text-muted-foreground">{String(label)}</div>
              </div>
            </div>
          );
        })}
      </div>

      <div className="rounded-xl border bg-card p-3 shadow-soft">
        <div className="flex flex-col gap-3 xl:flex-row xl:items-center">
          <Tabs value={kind} onValueChange={changeKind} className="shrink-0">
            <TabsList>
              <TabsTrigger value="chunks">{t("chunks")} · {summary?.chunks ?? 0}</TabsTrigger>
              <TabsTrigger value="events">{t("events")} · {summary?.events ?? 0}</TabsTrigger>
              <TabsTrigger value="entities">{t("entities")} · {summary?.entities ?? 0}</TabsTrigger>
            </TabsList>
          </Tabs>
          <div className="relative min-w-0 flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setPage(1);
              }}
              className="pl-9"
              placeholder={t(`search.${kind}`)}
            />
          </div>
          {kind !== "entities" && (
            <Select
              value={documentId}
              onValueChange={(value) => {
                setDocumentId(value);
                setPage(1);
              }}
            >
              <SelectTrigger className="w-full xl:w-64">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{t("allDocuments")}</SelectItem>
                {documents.map((document) => (
                  <SelectItem key={document.id} value={document.id}>{document.filename}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </div>
        {kind === "entities" && Boolean(summary?.entity_types.length) && (
          <div className="mt-3 flex flex-wrap gap-1.5 border-t pt-3">
            <span className="mr-1 inline-flex items-center gap-1 text-xs text-muted-foreground">
              <Tags className="size-3.5" />{t("entityTypes")}
            </span>
            {summary?.entity_types.map((item) => (
              <button
                key={item.type}
                type="button"
                onClick={() => toggleType(item.type)}
                className={cn(
                  "rounded-full border px-2.5 py-1 text-[11px] transition-colors",
                  entityTypes.includes(item.type)
                    ? "border-primary bg-primary text-primary-foreground"
                    : "bg-background hover:border-primary/50 hover:bg-primary/5",
                )}
              >
                {item.type} · {item.count}
              </button>
            ))}
          </div>
        )}
      </div>

      {error ? (
        <div className="rounded-xl border border-destructive/25 bg-destructive/5 p-4 text-sm text-destructive">{error}</div>
      ) : (
        <div className="grid min-h-[520px] gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
          <div className="min-w-0 rounded-xl border bg-card shadow-soft">
            <div className="flex items-center justify-between border-b px-4 py-3 text-xs text-muted-foreground">
              <span>{t("matched", { count: data?.total ?? 0 })}</span>
              <span>{t("page", { page, pages: pageCount })}</span>
            </div>
            <div className="divide-y">
              {data === null ? (
                Array.from({ length: 6 }).map((_, index) => <Skeleton key={index} className="m-3 h-20" />)
              ) : data.items.length === 0 ? (
                <div className="grid min-h-72 place-items-center p-6 text-sm text-muted-foreground">{t("empty")}</div>
              ) : data.items.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setSelected(item)}
                  className={cn(
                    "block w-full px-4 py-3 text-left transition-colors hover:bg-muted/45",
                    selected?.id === item.id && "bg-primary/[0.07]",
                  )}
                >
                  {isChunk(item) ? (
                    <>
                      <div className="flex items-center justify-between gap-3">
                        <span className="truncate text-sm font-medium">{item.heading || t("untitledChunk")}</span>
                        <Badge variant="outline" className="shrink-0">{t("eventCount", { count: item.event_count })}</Badge>
                      </div>
                      <p className="mt-1.5 line-clamp-2 text-xs leading-5 text-muted-foreground">{item.content}</p>
                    </>
                  ) : isEvent(item) ? (
                    <>
                      <div className="flex items-center gap-2">
                        <span className="truncate text-sm font-medium">{item.title}</span>
                        {item.category && <Badge variant="secondary">{item.category}</Badge>}
                      </div>
                      <p className="mt-1.5 line-clamp-2 text-xs leading-5 text-muted-foreground">{item.summary || item.content}</p>
                    </>
                  ) : isEntity(item) ? (
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium">{item.name}</div>
                        <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">{item.description || t("noDescription")}</p>
                      </div>
                      <div className="flex shrink-0 flex-col items-end gap-1">
                        <Badge variant="secondary">{item.type}</Badge>
                        <span className="text-[10px] text-muted-foreground">{t("eventCount", { count: item.event_count })}</span>
                      </div>
                    </div>
                  ) : null}
                </button>
              ))}
            </div>
            <div className="flex items-center justify-end gap-2 border-t p-3">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>
                <ChevronLeft className="size-3.5" />{t("previous")}
              </Button>
              <Button variant="outline" size="sm" disabled={page >= pageCount} onClick={() => setPage((value) => value + 1)}>
                {t("next")}<ChevronRight className="size-3.5" />
              </Button>
            </div>
          </div>

          <aside className="min-w-0 rounded-xl border bg-card shadow-soft lg:sticky lg:top-0 lg:h-fit">
            <div className="border-b px-4 py-3 text-sm font-medium">{t("details")}</div>
            {!selected ? (
              <div className="grid min-h-64 place-items-center p-6 text-center text-xs text-muted-foreground">{t("selectHint")}</div>
            ) : (
              <div className="max-h-[680px] space-y-4 overflow-y-auto p-4">
                <div>
                  <div className="flex flex-wrap gap-1.5">
                    {isEntity(selected) && <Badge>{selected.type}</Badge>}
                    {isEvent(selected) && selected.category && <Badge>{selected.category}</Badge>}
                    {(isChunk(selected) || isEvent(selected)) && selected.document_id && (
                      <Badge variant="outline">{documentNames.get(selected.document_id) ?? t("document")}</Badge>
                    )}
                  </div>
                  <h3 className="mt-2 break-words text-base font-semibold">
                    {isChunk(selected) ? selected.heading || t("untitledChunk") : isEvent(selected) ? selected.title : selected.name}
                  </h3>
                </div>
                {isEvent(selected) && selected.summary && (
                  <section>
                    <h4 className="text-xs font-medium text-muted-foreground">{t("summary")}</h4>
                    <p className="mt-1.5 whitespace-pre-wrap text-sm leading-6">{selected.summary}</p>
                  </section>
                )}
                {isEvent(selected) && selected.keywords.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">{selected.keywords.map((word) => <Badge key={word} variant="outline">{word}</Badge>)}</div>
                )}
                <section>
                  <h4 className="text-xs font-medium text-muted-foreground">{isEntity(selected) ? t("description") : t("content")}</h4>
                  <p className="mt-1.5 whitespace-pre-wrap break-words text-sm leading-6">
                    {isChunk(selected) ? selected.raw_content : isEvent(selected) ? selected.content : selected.description || t("noDescription")}
                  </p>
                </section>
                <div className="border-t pt-3 text-[11px] text-muted-foreground">ID · {selected.id}</div>
              </div>
            )}
          </aside>
        </div>
      )}
    </div>
  );
}
