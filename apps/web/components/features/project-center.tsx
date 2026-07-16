"use client";

import * as React from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import {
  ArrowRight,
  Bot,
  Boxes,
  Cable,
  DatabaseBackup,
  FileText,
  FolderKanban,
  Globe2,
  Network,
  Orbit,
  Plus,
  Sparkles,
} from "lucide-react";
import { motion } from "motion/react";

import type { Source } from "@/lib/types";
import { CreateSourceDialog } from "@/components/features/create-source-dialog";
import { EmptyState } from "@/components/features/empty-state";
import { PageHeader } from "@/components/features/page-header";
import { StructuredDataDialog } from "@/components/features/structured-data-dialog";
import { useKnowledgeWorkspace } from "@/components/features/knowledge-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

const CAPABILITY_CARDS = [
  { key: "agent", icon: Bot, href: "/chat", tone: "bg-primary/10 text-primary" },
  { key: "mcp", icon: Cable, href: "/connections", tone: "bg-blue-500/10 text-blue-600 dark:text-blue-400" },
  { key: "web", icon: Globe2, href: "/chat", tone: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400" },
  { key: "graph", icon: Orbit, href: "/knowledge", tone: "bg-violet-500/10 text-violet-600 dark:text-violet-400" },
] as const;

function ProjectCard({ source, onChanged }: { source: Source; onChanged: () => void | Promise<void> }) {
  const t = useTranslations("Projects");
  return (
    <motion.article
      layout
      whileHover={{ y: -3 }}
      transition={{ duration: 0.18 }}
      className="group overflow-hidden rounded-2xl border bg-card shadow-soft transition-shadow hover:shadow-lift"
    >
      <div className="border-b bg-gradient-to-br from-primary/[0.09] via-card to-blue-500/[0.04] p-5">
        <div className="flex items-start gap-3">
          <div className="grid size-11 shrink-0 place-items-center rounded-xl bg-primary text-primary-foreground shadow-soft">
            <FolderKanban className="size-5" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h2 className="truncate text-base font-semibold">{source.name}</h2>
            </div>
            <p className="mt-1 line-clamp-2 min-h-10 text-xs leading-5 text-muted-foreground">
              {source.description || t("projectFallback")}
            </p>
          </div>
        </div>
      </div>
      <div className="grid grid-cols-3 divide-x border-b py-3 text-center">
        <div>
          <div className="text-sm font-semibold tabular-nums">{source.document_count}</div>
          <div className="mt-0.5 text-[10px] text-muted-foreground">{t("documents")}</div>
        </div>
        <div>
          <div className="text-sm font-semibold tabular-nums">{source.chunk_count}</div>
          <div className="mt-0.5 text-[10px] text-muted-foreground">{t("chunks")}</div>
        </div>
        <div>
          <div className="text-sm font-semibold tabular-nums">{source.event_count}</div>
          <div className="mt-0.5 text-[10px] text-muted-foreground">{t("events")}</div>
        </div>
      </div>
      <div className="flex items-center gap-2 p-3">
        <StructuredDataDialog
          source={source}
          onImported={onChanged}
          trigger={
            <Button variant="outline" size="sm" className="flex-1">
              <DatabaseBackup className="size-3.5" />
              {t("data")}
            </Button>
          }
        />
        <Button asChild size="sm" className="flex-1">
          <Link href={`/knowledge/${source.id}`}>
            <Network className="size-3.5" />
            {t("open")}
          </Link>
        </Button>
      </div>
    </motion.article>
  );
}

export function ProjectCenter() {
  const t = useTranslations("Projects");
  const { sources, error, ensureLoaded, refresh, addSource } = useKnowledgeWorkspace();

  React.useEffect(() => {
    void ensureLoaded();
  }, [ensureLoaded]);

  const totals = React.useMemo(() => ({
    documents: sources?.reduce((sum, source) => sum + source.document_count, 0) ?? 0,
    chunks: sources?.reduce((sum, source) => sum + source.chunk_count, 0) ?? 0,
    events: sources?.reduce((sum, source) => sum + source.event_count, 0) ?? 0,
  }), [sources]);

  return (
    <div className="chemclaw-projects min-h-full p-4 md:p-6">
      <div className="mx-auto max-w-7xl space-y-6">
        <PageHeader
          title={t("title")}
          description={t("description")}
          actions={
            <CreateSourceDialog
              mode="project"
              onCreated={addSource}
              trigger={
                <Button>
                  <Plus className="size-4" />
                  {t("newProject")}
                </Button>
              }
            />
          }
        />

        <section className="relative overflow-hidden rounded-2xl border bg-card px-5 py-5 shadow-soft md:px-6">
          <div className="chemclaw-molecule-grid absolute inset-0 opacity-40" />
          <div className="relative flex flex-col gap-5 lg:flex-row lg:items-center">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <Badge className="bg-primary text-primary-foreground">CHEMCLAW OS</Badge>
              </div>
              <h1 className="mt-3 max-w-2xl text-2xl font-semibold tracking-tight md:text-3xl">
                {t("heroTitle")}
              </h1>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">{t("heroDescription")}</p>
            </div>
            <div className="grid shrink-0 grid-cols-3 gap-2">
              {[
                [FileText, totals.documents, t("documents")],
                [Boxes, totals.chunks, t("chunks")],
                [Sparkles, totals.events, t("events")],
              ].map(([Icon, value, label]) => {
                const MetricIcon = Icon as typeof FileText;
                return (
                  <div key={String(label)} className="min-w-24 rounded-xl border bg-card/85 p-3 text-center backdrop-blur-sm">
                    <MetricIcon className="mx-auto size-4 text-primary" />
                    <div className="mt-1 text-lg font-semibold tabular-nums">{String(value)}</div>
                    <div className="text-[10px] text-muted-foreground">{String(label)}</div>
                  </div>
                );
              })}
            </div>
          </div>
        </section>

        <section>
          <div className="mb-3 flex items-center justify-between">
            <div>
              <h2 className="text-sm font-semibold">{t("capabilitiesTitle")}</h2>
              <p className="mt-0.5 text-xs text-muted-foreground">{t("capabilitiesDescription")}</p>
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {CAPABILITY_CARDS.map((item) => {
              const Icon = item.icon;
              return (
                <Link
                  key={item.key}
                  href={item.href}
                  className="group flex items-center gap-3 rounded-xl border bg-card p-3.5 shadow-soft transition-all hover:-translate-y-0.5 hover:shadow-lift"
                >
                  <div className={`grid size-10 shrink-0 place-items-center rounded-xl ${item.tone}`}>
                    <Icon className="size-4.5" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium">{t(`capabilities.${item.key}.title`)}</div>
                    <div className="truncate text-[11px] text-muted-foreground">{t(`capabilities.${item.key}.description`)}</div>
                  </div>
                  <ArrowRight className="size-3.5 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
                </Link>
              );
            })}
          </div>
        </section>

        <section>
          <div className="mb-3 flex items-center justify-between">
            <div>
              <h2 className="text-sm font-semibold">{t("projectsTitle")}</h2>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {t("projectsCount", { count: sources?.length ?? 0 })}
              </p>
            </div>
          </div>
          {error ? (
            <div className="rounded-xl border border-destructive/25 bg-destructive/5 p-4 text-sm text-destructive">
              {error}
              <Button variant="outline" size="sm" className="ml-3" onClick={() => void refresh()}>{t("retry")}</Button>
            </div>
          ) : sources === null ? (
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {Array.from({ length: 3 }).map((_, index) => <Skeleton key={index} className="h-64 rounded-2xl" />)}
            </div>
          ) : sources.length === 0 ? (
            <EmptyState
              icon={FolderKanban}
              title={t("emptyTitle")}
              description={t("emptyDescription")}
              action={<CreateSourceDialog mode="project" onCreated={addSource} />}
            />
          ) : (
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {sources.map((source) => (
                <ProjectCard key={source.id} source={source} onChanged={refresh} />
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
