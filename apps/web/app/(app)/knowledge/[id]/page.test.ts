import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const detailPage = readFileSync(new URL("./page.tsx", import.meta.url), "utf8");
const reader = readFileSync(
  new URL("../../../../components/features/project-data-reader.tsx", import.meta.url),
  "utf8",
);
const detailPanel = readFileSync(
  new URL("../../../../components/features/detail-panel.tsx", import.meta.url),
  "utf8",
);

describe("source detail scrolling layout", () => {
  it("only constrains graph views to the viewport and lets the reader grow", () => {
    expect(detailPage).toContain('graphViewActive && "flex h-full min-h-0 flex-col overflow-hidden"');
    expect(detailPage).toContain('graphViewActive && "min-h-0 flex-1 overflow-hidden"');
    expect(detailPage).toContain('graphViewActive && "flex min-h-0 flex-1 flex-col"');
    expect(detailPage).not.toContain('wideViewActive && "flex h-full min-h-0 flex-col overflow-hidden"');
    expect(detailPage).toContain('wideViewActive ? "max-w-none md:p-5" : "max-w-4xl md:p-6"');
    expect(reader).toContain('className="flex min-w-0 flex-col gap-4"');
    expect(detailPanel).toContain('className="h-full min-w-0 overflow-y-auto overscroll-contain"');
  });

  it("retains list pagination and graph canvas sizing", () => {
    expect(reader).toContain('onClick={() => setPage((value) => value + 1)}');
    expect(reader).toContain('onClick={() => setPage((value) => value - 1)}');
    expect(detailPage).toContain('<div className="min-h-0 flex-1">');
    expect(detailPage).toContain('mode={contentView === "graph3d" ? "3d" : "2d"}');
  });
});
