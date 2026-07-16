"use client";

import * as React from "react";
import { useTranslations } from "next-intl";
import {
  CheckCircle2,
  DatabaseBackup,
  Download,
  FileJson2,
  ShieldCheck,
  TriangleAlert,
  Upload,
} from "lucide-react";
import { toast } from "sonner";

import { api, ApiError } from "@/lib/api";
import type { Source, StructuredImportResult } from "@/lib/types";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type ImportFormat = "auto" | "json" | "csv" | "markdown";
type ExportFormat = "json" | "csv" | "markdown";
type ExportProfile = "simple" | "full";

function saveBlob(blob: Blob, filename: string) {
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(href);
}

export function StructuredDataDialog({
  source,
  onImported,
  trigger,
}: {
  source: Source;
  onImported?: () => void | Promise<void>;
  trigger?: React.ReactNode;
}) {
  const t = useTranslations("StructuredData");
  const [open, setOpen] = React.useState(false);
  const [file, setFile] = React.useState<File | null>(null);
  const [format, setFormat] = React.useState<ImportFormat>("auto");
  const [profile, setProfile] = React.useState<ExportProfile>("full");
  const [progress, setProgress] = React.useState(0);
  const [busy, setBusy] = React.useState(false);
  const [downloading, setDownloading] = React.useState("");
  const [result, setResult] = React.useState<StructuredImportResult | null>(null);

  async function runImport(selectedFile: File) {
    setFile(selectedFile);
    setBusy(true);
    setProgress(1);
    setResult(null);
    try {
      let job = await api.importStructuredData(
        source.id,
        selectedFile,
        format,
        (value) => setProgress(Math.max(1, Math.round(value * 0.2))),
      );
      setProgress(Math.max(20, Math.round(20 + job.progress * 80)));
      while (job.status === "queued" || job.status === "running") {
        await new Promise((resolve) => window.setTimeout(resolve, 700));
        job = await api.getJob(job.id);
        setProgress(Math.max(20, Math.round(20 + job.progress * 80)));
      }
      if (job.status === "failed") {
        throw new ApiError(422, "structured_import_failed", job.error || t("importFailed"));
      }
      const imported = job.payload.result as StructuredImportResult | undefined;
      if (!imported) {
        throw new ApiError(500, "structured_import_failed", t("importFailed"));
      }
      setProgress(100);
      setResult(imported);
      await onImported?.();
      toast.success(t("importSuccess", { count: imported.counts.documents }));
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : t("importFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function runExport(exportFormat: ExportFormat) {
    const key = `${exportFormat}-${profile}`;
    setDownloading(key);
    try {
      const blob = await api.exportStructuredData(source.id, exportFormat, profile);
      const extension = exportFormat === "markdown" ? "md" : exportFormat;
      saveBlob(blob, `${source.name}-ChemClaw-${profile}.${extension}`);
      toast.success(t("exportSuccess"));
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : t("exportFailed"));
    } finally {
      setDownloading("");
    }
  }

  async function runBackup() {
    setDownloading("backup");
    try {
      const blob = await api.backupSource(source.id);
      saveBlob(blob, `${source.name}-ChemClaw-backup.zip`);
      toast.success(t("backupSuccess"));
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : t("backupFailed"));
    } finally {
      setDownloading("");
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        {trigger ?? (
          <Button variant="outline">
            <DatabaseBackup className="size-4" />
            {t("open")}
          </Button>
        )}
      </DialogTrigger>
      <DialogContent className="max-h-[88vh] max-w-3xl overflow-y-auto p-0">
        <DialogHeader className="border-b bg-gradient-to-r from-primary/10 via-card to-card px-6 py-5 text-left">
          <div className="mb-1 flex items-center gap-2">
            <Badge className="bg-primary/12 text-primary hover:bg-primary/12">CHEMCLAW</Badge>
          </div>
          <DialogTitle className="text-xl">{t("title")}</DialogTitle>
          <DialogDescription>{t("description", { name: source.name })}</DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 p-6 md:grid-cols-2">
          <section className="rounded-xl border bg-card p-4 shadow-soft">
            <div className="flex items-start gap-3">
              <div className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary">
                <Upload className="size-4" />
              </div>
              <div>
                <h3 className="text-sm font-semibold">{t("importTitle")}</h3>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">{t("importDescription")}</p>
              </div>
            </div>

            <label className="mt-4 flex min-h-28 cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-primary/30 bg-primary/[0.035] px-4 py-5 text-center transition-colors hover:bg-primary/[0.07]">
              <FileJson2 className="size-6 text-primary" />
              <span className="mt-2 max-w-full truncate text-sm font-medium">
                {file?.name ?? t("chooseFile")}
              </span>
              <span className="mt-1 text-[11px] text-muted-foreground">JSON · CSV/TSV · Markdown</span>
              <input
                type="file"
                className="sr-only"
                accept=".json,.csv,.tsv,.md,.markdown,application/json,text/csv,text/markdown"
                disabled={busy}
                onChange={(event) => {
                  const selected = event.target.files?.[0];
                  event.currentTarget.value = "";
                  if (selected) void runImport(selected);
                }}
              />
            </label>
            <div className="mt-3 flex gap-2">
              <Select value={format} onValueChange={(value) => setFormat(value as ImportFormat)}>
                <SelectTrigger className="h-9 flex-1">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="auto">{t("formatAuto")}</SelectItem>
                  <SelectItem value="json">JSON</SelectItem>
                  <SelectItem value="csv">CSV / TSV</SelectItem>
                  <SelectItem value="markdown">Markdown</SelectItem>
                </SelectContent>
              </Select>
              <div className="flex min-w-28 items-center justify-center rounded-md border bg-muted/35 px-3 text-xs text-muted-foreground">
                {busy ? t("importing") : t("autoStart")}
              </div>
            </div>
            {busy && <Progress value={progress} className="mt-3 h-1.5" />}
          </section>

          <section className="rounded-xl border bg-card p-4 shadow-soft">
            <div className="flex items-start gap-3">
              <div className="grid size-9 shrink-0 place-items-center rounded-lg bg-blue-500/10 text-blue-600 dark:text-blue-400">
                <Download className="size-4" />
              </div>
              <div>
                <h3 className="text-sm font-semibold">{t("exportTitle")}</h3>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">{t("exportDescription")}</p>
              </div>
            </div>
            <Select value={profile} onValueChange={(value) => setProfile(value as ExportProfile)}>
              <SelectTrigger className="mt-4 h-9 w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="full">{t("profileFull")}</SelectItem>
                <SelectItem value="simple">{t("profileSimple")}</SelectItem>
              </SelectContent>
            </Select>
            <div className="mt-3 grid grid-cols-3 gap-2">
              {(["json", "csv", "markdown"] as const).map((item) => (
                <Button
                  key={item}
                  variant="outline"
                  size="sm"
                  disabled={Boolean(downloading)}
                  onClick={() => void runExport(item)}
                >
                  {downloading === `${item}-${profile}` ? t("preparing") : item === "markdown" ? "MD" : item.toUpperCase()}
                </Button>
              ))}
            </div>
            <Button
              variant="secondary"
              className="mt-3 w-full"
              disabled={Boolean(downloading)}
              onClick={() => void runBackup()}
            >
              <DatabaseBackup className="size-4" />
              {downloading === "backup" ? t("preparing") : t("backup")}
            </Button>
            <p className="mt-2 text-[11px] leading-4 text-muted-foreground">{t("backupDescription")}</p>
          </section>
        </div>

        <div className="px-6 pb-6">
          <Alert className="border-primary/20 bg-primary/[0.035]">
            <ShieldCheck className="size-4 text-primary" />
            <AlertTitle>{t("guaranteeTitle")}</AlertTitle>
            <AlertDescription>{t("guaranteeDescription")}</AlertDescription>
          </Alert>
          {result && (
            <Alert className="mt-3 border-success/25 bg-success/5">
              <CheckCircle2 className="size-4 text-success" />
              <AlertTitle>{t("resultTitle")}</AlertTitle>
              <AlertDescription>
                {t("resultCounts", result.counts)}
                {result.warnings.map((warning) => (
                  <span key={warning} className="mt-1 flex items-start gap-1.5 text-amber-700 dark:text-amber-300">
                    <TriangleAlert className="mt-0.5 size-3 shrink-0" />
                    {warning}
                  </span>
                ))}
              </AlertDescription>
            </Alert>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
