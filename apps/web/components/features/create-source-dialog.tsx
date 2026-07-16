"use client";

import * as React from "react";
import { Plus } from "lucide-react";
import { useTranslations } from "next-intl";

import type { Source } from "@/lib/types";
import { SourceCreateForm } from "@/components/features/source-create-form";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

export function CreateSourceDialog({
  onCreated,
  trigger,
  mode = "source",
}: {
  onCreated: (s: Source) => void;
  trigger?: React.ReactNode;
  mode?: "source" | "project";
}) {
  const t = useTranslations("SourceForm");
  const projectsT = useTranslations("Projects");
  const [open, setOpen] = React.useState(false);
  const title = mode === "project" ? projectsT("newProject") : t("new");
  const description = mode === "project" ? projectsT("createDialogDescription") : t("dialogDescription");

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        {trigger ?? (
          <Button>
            <Plus className="size-4" />
            {title}
          </Button>
        )}
      </DialogTrigger>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        <SourceCreateForm
          mode={mode}
          onCreated={(source) => {
            onCreated(source);
            setOpen(false);
          }}
        />
      </DialogContent>
    </Dialog>
  );
}
