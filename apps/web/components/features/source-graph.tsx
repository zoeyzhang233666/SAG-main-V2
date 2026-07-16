"use client";

import * as React from "react";
import dynamic from "next/dynamic";
import { useLocale, useTranslations } from "next-intl";
import { type Edge, type Node, type NodeProps } from "@xyflow/react";
import {
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force";
import {
  AlertTriangle,
  Filter,
  FileText,
  Network,
  RefreshCw,
  Sparkles,
  Tag,
  Users,
  X,
} from "lucide-react";

import { api, ApiError } from "@/lib/api";
import type { Entity, Source, SourceGraphChunk, SourceGraphEvent, SourceGraphResponse } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useDetailPanel } from "@/components/features/detail-panel";
import {
  eventEntityNodeId,
  sliceEventEntityGraph,
  type EventEntityGraphKind,
  type EventEntityGraphSlice,
} from "@/components/features/event-entity-graph-model";
import {
  EventEntitySelectionCard,
  type EventEntitySelection,
} from "@/components/features/event-entity-selection-card";
import {
  GRAPH_EDGE_TYPE,
  GraphCanvas,
  GraphHandles,
  GraphLegend,
  graphEdgeHandles,
  type GraphLayout,
  type GraphPoint,
} from "@/components/features/graph-canvas";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";

const OrbitalEventEntityGraph = dynamic(
  () =>
    import("@/components/features/orbital-graph-3d").then(
      (module) => module.OrbitalEventEntityGraph,
    ),
  {
    ssr: false,
    loading: () => <Skeleton className="h-full min-h-[520px]" />,
  },
);

type GraphKind = EventEntityGraphKind | "chunk";

interface GraphNodeData extends Record<string, unknown> {
  kind: GraphKind;
  label: string;
  subtitle?: string;
  event?: SourceGraphEvent;
  entity?: Entity;
  chunk?: SourceGraphChunk;
}

interface GraphLabels {
  timedEvent: string;
  extractedEvent: string;
  unnamedEntity: string;
  uncategorized: string;
  untitledChunk: string;
}

const KIND_META: Record<
  GraphKind,
  { titleKey: "chunk" | "event" | "entity"; width: number; className: string; header: string }
> = {
  chunk: {
    titleKey: "chunk",
    width: 212,
    className: "border-sky-500/35",
    header: "bg-sky-500/12 text-sky-700 dark:text-sky-300",
  },
  event: {
    titleKey: "event",
    width: 196,
    className: "border-amber-500/30",
    header: "bg-amber-500/12 text-amber-700 dark:text-amber-300",
  },
  entity: {
    titleKey: "entity",
    width: 148,
    className: "border-dashed border-violet-500/35",
    header: "bg-violet-500/10 text-violet-700 dark:text-violet-300",
  },
};

function GraphNode({ data, selected }: NodeProps) {
  const t = useTranslations("SourceGraph");
  const node = data as GraphNodeData;
  const meta = KIND_META[node.kind];
  return (
    <div
      className={cn(
        "relative cursor-grab overflow-hidden rounded-lg border bg-card shadow-soft transition-[box-shadow,border-color] hover:shadow-lift active:cursor-grabbing",
        meta.className,
        selected && "ring-2 ring-primary/45 ring-offset-2 ring-offset-background",
      )}
      style={{ width: meta.width }}
    >
      <GraphHandles />
      <div className={cn("flex items-center gap-1.5 px-2 py-1 text-[10px] font-medium", meta.header)}>
        {node.kind === "chunk" ? (
          <FileText className="size-3 shrink-0" />
        ) : node.kind === "event" ? (
          <Sparkles className="size-3 shrink-0" />
        ) : (
          <Users className="size-3 shrink-0" />
        )}
        {t(meta.titleKey)}
      </div>
      <div className="px-2.5 py-2">
        <div
          className="line-clamp-2 text-xs font-medium leading-snug text-foreground"
          title={node.label}
        >
          {node.label}
        </div>
        {node.subtitle && (
          <div className="mt-1 truncate text-[10px] text-muted-foreground" title={node.subtitle}>
            {node.subtitle}
          </div>
        )}
      </div>
    </div>
  );
}

const nodeTypes = { sourceGraph: GraphNode };
const DEFAULT_GRAPH_LIMIT = 1_000;
const GRAPH_LIMIT_STORAGE_KEY = "sag:source-graph-limit";
const GRAPH_LIMIT_OPTIONS = [100, 300, 600, 1_000, 2_000, 5_000, 10_000] as const;

function makeNodes(
  slice: EventEntityGraphSlice,
  positions: Map<string, GraphPoint>,
  labels: GraphLabels,
): Node[] {
  const fallback = { x: 0, y: 0 };
  return [
    ...slice.events.map((event) => ({
      id: eventEntityNodeId("event", event.id),
      type: "sourceGraph",
      position: positions.get(eventEntityNodeId("event", event.id)) ?? fallback,
      data: {
        kind: "event",
        label: event.title,
        subtitle: event.category || (event.start_time ? labels.timedEvent : labels.extractedEvent),
        event,
      } satisfies GraphNodeData,
    })),
    ...slice.entities.map((entity) => ({
      id: eventEntityNodeId("entity", entity.id),
      type: "sourceGraph",
      position: positions.get(eventEntityNodeId("entity", entity.id)) ?? fallback,
      data: {
        kind: "entity",
        label: entity.name || labels.unnamedEntity,
        subtitle: entity.type || labels.uncategorized,
        entity,
      } satisfies GraphNodeData,
    })),
  ];
}

function makeEdges(
  slice: EventEntityGraphSlice,
  positions: Map<string, GraphPoint>,
  layout: GraphLayout,
): Edge[] {
  return slice.relations.map((relation) => {
    const source = eventEntityNodeId("event", relation.eventId);
    const target = eventEntityNodeId("entity", relation.entityId);
    const from = positions.get(source) ?? { x: 0, y: 0 };
    const to = positions.get(target) ?? { x: 0, y: 0 };
    return {
      id: relation.id,
      source,
      target,
      ...graphEdgeHandles(from, to),
      type: GRAPH_EDGE_TYPE[layout],
      interactionWidth: 14,
      style: {
        stroke: "hsl(263 55% 58% / 0.32)",
        strokeWidth: 1.25,
      },
    };
  });
}

function linkedEventPositions(
  slice: EventEntityGraphSlice,
  positions: Map<string, GraphPoint>,
  entityId: string,
) {
  return slice.relations
    .filter((relation) => relation.entityId === entityId)
    .map((relation) => positions.get(eventEntityNodeId("event", relation.eventId)))
    .filter((position): position is GraphPoint => Boolean(position));
}

function buildTreePositions(slice: EventEntityGraphSlice, locale: string) {
  const positions = new Map<string, GraphPoint>();
  const eventGap = 260;
  const eventStart = -((slice.events.length - 1) * eventGap) / 2;
  slice.events.forEach((event, index) => {
    positions.set(eventEntityNodeId("event", event.id), {
      x: eventStart + index * eventGap,
      y: 0,
    });
  });

  const entities = slice.entities
    .map((entity) => {
      const linked = linkedEventPositions(slice, positions, entity.id);
      return {
        entity,
        desiredX: linked.length
          ? linked.reduce((sum, position) => sum + position.x, 0) / linked.length
          : 0,
      };
    })
    .sort((a, b) => a.desiredX - b.desiredX || a.entity.name.localeCompare(b.entity.name, locale));
  let previousX = -Infinity;
  entities.forEach(({ entity, desiredX }) => {
    const x = Math.max(desiredX, previousX + 174);
    previousX = x;
    positions.set(eventEntityNodeId("entity", entity.id), { x, y: 310 });
  });
  if (entities.length > 0) {
    const first = positions.get(eventEntityNodeId("entity", entities[0].entity.id))?.x ?? 0;
    const last =
      positions.get(eventEntityNodeId("entity", entities[entities.length - 1].entity.id))?.x ?? 0;
    const offset = (first + last) / 2;
    entities.forEach(({ entity }) => {
      const id = eventEntityNodeId("entity", entity.id);
      const position = positions.get(id);
      if (position) positions.set(id, { ...position, x: position.x - offset });
    });
  }
  return positions;
}

function normalizeAngle(angle: number) {
  const tau = Math.PI * 2;
  return ((angle % tau) + tau) % tau;
}

function buildRadialPositions(slice: EventEntityGraphSlice, locale: string) {
  const tau = Math.PI * 2;
  const positions = new Map<string, GraphPoint>();
  const eventCount = slice.events.length;
  const eventRadius = eventCount <= 1 ? 0 : Math.max(250, (eventCount * 220 * 1.16) / tau);
  const eventAngles = new Map<string, number>();

  slice.events.forEach((event, index) => {
    const angle = -Math.PI / 2 + (index * tau) / Math.max(eventCount, 1);
    eventAngles.set(event.id, angle);
    positions.set(eventEntityNodeId("event", event.id), {
      x: Math.cos(angle) * eventRadius,
      y: Math.sin(angle) * eventRadius,
    });
  });

  const entities = slice.entities
    .map((entity, index) => {
      const angles = slice.relations
        .filter((relation) => relation.entityId === entity.id)
        .map((relation) => eventAngles.get(relation.eventId))
        .filter((angle): angle is number => angle != null);
      const desired = angles.length
        ? Math.atan2(
            angles.reduce((sum, angle) => sum + Math.sin(angle), 0),
            angles.reduce((sum, angle) => sum + Math.cos(angle), 0),
          )
        : -Math.PI / 2 + (index * tau) / Math.max(slice.entities.length, 1);
      return { entity, angle: normalizeAngle(desired) };
    })
    .sort((a, b) => a.angle - b.angle || a.entity.name.localeCompare(b.entity.name, locale));

  const minGap = Math.min(0.18, (tau * 0.88) / Math.max(entities.length, 1));
  let previousAngle = -Infinity;
  entities.forEach((item) => {
    item.angle = Math.max(item.angle, previousAngle + minGap);
    previousAngle = item.angle;
  });
  if (entities.length && entities[entities.length - 1].angle - entities[0].angle > tau - minGap) {
    const start = entities[0].angle;
    entities.forEach((item, index) => {
      item.angle = start + (index * tau) / entities.length;
    });
  }

  const entityRadius = Math.max(360, eventRadius + 390);
  entities.forEach(({ entity, angle }) => {
    positions.set(eventEntityNodeId("entity", entity.id), {
      x: Math.cos(angle) * entityRadius,
      y: Math.sin(angle) * entityRadius,
    });
  });
  return positions;
}

function collisionRadius(kind: GraphKind) {
  return kind === "event" ? 126 : kind === "chunk" ? 130 : 80;
}

function withChunkLayer(
  network: { nodes: Node[]; edges: Edge[] },
  graph: SourceGraphResponse,
  layout: GraphLayout,
  labels: GraphLabels,
) {
  const eventByChunk = new Map<string, SourceGraphEvent[]>();
  graph.events.forEach((event) => {
    if (!event.chunk_id) return;
    eventByChunk.set(event.chunk_id, [...(eventByChunk.get(event.chunk_id) ?? []), event]);
  });
  const eventPositions = new Map(network.nodes.map((node) => [node.id, node.position]));
  const chunks = (graph.chunks ?? []).filter((chunk) => eventByChunk.has(chunk.id));
  const chunkNodes: Node[] = chunks.map((chunk, index) => {
    const linked = eventByChunk.get(chunk.id) ?? [];
    const linkedPositions = linked
      .map((event) => eventPositions.get(eventEntityNodeId("event", event.id)))
      .filter((position): position is GraphPoint => Boolean(position));
    const center = linkedPositions.length
      ? {
          x: linkedPositions.reduce((sum, point) => sum + point.x, 0) / linkedPositions.length,
          y: linkedPositions.reduce((sum, point) => sum + point.y, 0) / linkedPositions.length,
        }
      : { x: 0, y: 0 };
    let position: GraphPoint;
    if (layout === "tree") {
      position = { x: center.x + ((index % 3) - 1) * 24, y: -300 - (index % 2) * 38 };
    } else {
      const angle = Math.atan2(center.y || index + 1, center.x || chunks.length - index);
      const linkedRadius = Math.hypot(center.x, center.y);
      const radius = Math.max(150, linkedRadius * 0.56) + (index % 3) * 34;
      position = { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius };
    }
    return {
      id: `chunk:${chunk.id}`,
      type: "sourceGraph",
      position,
      data: {
        kind: "chunk",
        label: chunk.heading || labels.untitledChunk,
        subtitle: `${chunk.event_count}`,
        chunk,
      } satisfies GraphNodeData,
    };
  });
  const chunkEdges: Edge[] = chunks.flatMap((chunk) =>
    (eventByChunk.get(chunk.id) ?? []).map((event) => {
      const source = `chunk:${chunk.id}`;
      const target = eventEntityNodeId("event", event.id);
      const from = chunkNodes.find((node) => node.id === source)?.position ?? { x: 0, y: 0 };
      const to = eventPositions.get(target) ?? { x: 0, y: 0 };
      return {
        id: `chunk-event:${chunk.id}:${event.id}`,
        source,
        target,
        ...graphEdgeHandles(from, to),
        type: GRAPH_EDGE_TYPE[layout],
        interactionWidth: 14,
        animated: true,
        style: { stroke: "hsl(199 82% 48% / 0.36)", strokeWidth: 1.35, strokeDasharray: "5 5" },
      };
    }),
  );
  return { nodes: [...network.nodes, ...chunkNodes], edges: [...network.edges, ...chunkEdges] };
}

function buildNetwork(
  slice: EventEntityGraphSlice,
  layout: GraphLayout,
  labels: GraphLabels,
  locale: string,
): { nodes: Node[]; edges: Edge[] } {
  if (layout === "tree") {
    const positions = buildTreePositions(slice, locale);
    return {
      nodes: makeNodes(slice, positions, labels),
      edges: makeEdges(slice, positions, layout),
    };
  }

  const radialPositions = buildRadialPositions(slice, locale);
  const radialNodes = makeNodes(slice, radialPositions, labels);
  if (layout === "radial") {
    return {
      nodes: radialNodes,
      edges: makeEdges(slice, radialPositions, layout),
    };
  }

  type SimNode = SimulationNodeDatum & {
    id: string;
    kind: GraphKind;
  };
  const degree = new Map<string, number>();
  slice.relations.forEach((relation) => {
    const event = eventEntityNodeId("event", relation.eventId);
    const entity = eventEntityNodeId("entity", relation.entityId);
    degree.set(event, (degree.get(event) ?? 0) + 1);
    degree.set(entity, (degree.get(entity) ?? 0) + 1);
  });
  const simNodes: SimNode[] = radialNodes.map((node) => ({
    id: node.id,
    kind: (node.data as GraphNodeData).kind,
    x: node.position.x * 0.48,
    y: node.position.y * 0.48,
  }));
  const seedEdges = makeEdges(slice, radialPositions, "force");
  const simLinks: SimulationLinkDatum<SimNode>[] = seedEdges.map((edge) => ({
    source: edge.source,
    target: edge.target,
  }));
  // Force simulation is quadratic and becomes the dominant UI cost on larger
  // graphs. The deterministic radial layout remains readable and avoids a long
  // main-thread stall once the working set crosses this threshold.
  if (simNodes.length > 280) {
    return {
      nodes: radialNodes,
      edges: makeEdges(slice, radialPositions, layout),
    };
  }

  const simulation = forceSimulation<SimNode>(simNodes)
    .force(
      "link",
      forceLink<SimNode, SimulationLinkDatum<SimNode>>(simLinks)
        .id((node) => node.id)
        .distance((link) => {
          const source = typeof link.source === "object" ? link.source.id : String(link.source);
          const target = typeof link.target === "object" ? link.target.id : String(link.target);
          return (
            164 +
            Math.min(
              64,
              Math.max(degree.get(source) ?? 1, degree.get(target) ?? 1) * 3.5,
            )
          );
        })
        .strength(0.46),
    )
    .force(
      "charge",
      forceManyBody<SimNode>()
        .strength((node) => (node.kind === "event" ? -430 : -135))
        .distanceMax(1500),
    )
    .force(
      "collide",
      forceCollide<SimNode>((node) => collisionRadius(node.kind)).strength(0.96).iterations(4),
    )
    .force(
      "x",
      forceX<SimNode>(0).strength((node) => (node.kind === "event" ? 0.028 : 0.018)),
    )
    .force(
      "y",
      forceY<SimNode>(0).strength((node) => (node.kind === "event" ? 0.028 : 0.018)),
    )
    .stop();
  for (let index = 0; index < 340; index += 1) simulation.tick();
  const positions = new Map(
    simNodes.map((node) => [node.id, { x: node.x ?? 0, y: node.y ?? 0 }]),
  );
  return {
    nodes: makeNodes(slice, positions, labels),
    edges: makeEdges(slice, positions, layout),
  };
}

export function EventEntityGraph({
  graph,
  onOpenEvent,
  onOpenChunk,
  toolbarActions,
  refreshKey = "graph",
  emptyTitle,
  emptyDescription,
  flowClassName = "sag-source-graph",
}: {
  graph: SourceGraphResponse;
  onOpenEvent?: (event: SourceGraphEvent) => void;
  onOpenChunk?: (chunk: SourceGraphChunk) => void;
  toolbarActions?: React.ReactNode;
  refreshKey?: React.Key;
  emptyTitle?: string;
  emptyDescription?: string;
  flowClassName?: string;
}) {
  const t = useTranslations("SourceGraph");
  const locale = useLocale();
  const [selection, setSelection] = React.useState<EventEntitySelection | null>(null);
  const [layout, setLayout] = React.useState<GraphLayout>("force");

  React.useEffect(() => {
    const saved = window.localStorage.getItem("sag:source-graph-layout");
    if (saved === "radial" || saved === "tree" || saved === "force") {
      setLayout(saved);
    } else {
      window.localStorage.setItem("sag:source-graph-layout", "force");
    }
  }, []);
  const changeLayout = (next: GraphLayout) => {
    setLayout(next);
    window.localStorage.setItem("sag:source-graph-layout", next);
  };

  React.useEffect(() => setSelection(null), [graph]);

  const slice = React.useMemo(() => sliceEventEntityGraph(graph), [graph]);
  const labels = React.useMemo<GraphLabels>(() => ({
    timedEvent: t("timedEvent"),
    extractedEvent: t("extractedEvent"),
    unnamedEntity: t("unnamedEntity"),
    uncategorized: t("uncategorized"),
    untitledChunk: t("untitledChunk"),
  }), [t]);
  const network = React.useMemo(
    () => withChunkLayer(buildNetwork(slice, layout, labels, locale), graph, layout, labels),
    [graph, labels, layout, locale, slice],
  );
  const empty = slice.relations.length === 0;
  const resolvedEmptyTitle =
    emptyTitle ??
    (graph.documents.length === 0 && graph.events.length === 0
      ? t("emptyDocuments")
      : t("emptyRelations"));
  const resolvedEmptyDescription =
    emptyDescription ??
    (graph.documents.length === 0 && graph.events.length === 0
      ? t("emptyDocumentsDescription")
      : t("emptyRelationsDescription"));

  return (
    <GraphCanvas
      nodes={network.nodes}
      edges={network.edges}
      nodeTypes={nodeTypes}
      layout={layout}
      onLayoutChange={changeLayout}
      legend={
        <GraphLegend
          items={[
            { label: t("chunk"), className: "bg-sky-500" },
            { label: t("event"), className: "bg-amber-500" },
            {
              label: t("entity"),
              className: "border border-dashed border-violet-500 bg-violet-500/20",
            },
          ]}
        >
          <div
            className={cn(
              "mt-1.5 flex items-center gap-1 text-[10px] text-muted-foreground",
              graph.truncated && "text-amber-700 dark:text-amber-300",
            )}
          >
            {graph.truncated && <AlertTriangle className="size-3 shrink-0" />}
            <span>
              {graph.truncated
                ? t("statsTruncated", {
                    shownEvents: slice.events.length,
                    events: graph.counts.events,
                    shownEntities: slice.entities.length,
                    entities: graph.counts.entities,
                    relations: slice.relations.length,
                  })
                : t("stats", {
                    events: slice.events.length,
                    entities: slice.entities.length,
                    relations: slice.relations.length,
                  })}
            </span>
          </div>
        </GraphLegend>
      }
      toolbarActions={toolbarActions}
      heightClassName="h-full min-h-0"
      fitPadding={0.18}
      fitMinZoom={0.12}
      minZoom={0.08}
      maxZoom={1.8}
      elementsSelectable
      onlyRenderVisibleElements
      refreshKey={`${slice.relations.length}-${String(refreshKey)}`}
      ariaLabel={t("aria")}
      flowClassName={flowClassName}
      onPaneClick={() => setSelection(null)}
      onNodeClick={(_event, node) => {
        const data = node.data as GraphNodeData;
        if (data.kind === "event" && data.event) {
          setSelection({ kind: "event", value: data.event });
        } else if (data.kind === "entity" && data.entity) {
          setSelection({ kind: "entity", value: data.entity });
        } else if (data.kind === "chunk" && data.chunk) {
          setSelection(null);
          onOpenChunk?.(data.chunk);
        }
      }}
    >
      {selection && (
        <EventEntitySelectionCard selection={selection} onOpenEvent={onOpenEvent} />
      )}
      {empty && (
        <div className="pointer-events-none absolute inset-x-12 bottom-5 z-10 mx-auto max-w-md rounded-lg border bg-card/95 px-4 py-3 text-center shadow-soft backdrop-blur-sm">
          <div className="flex items-center justify-center gap-1.5 text-xs font-medium text-foreground">
            {graph.documents.length === 0 && graph.events.length === 0 ? (
              <FileText className="size-3.5" />
            ) : (
              <Tag className="size-3.5" />
            )}
            {resolvedEmptyTitle}
          </div>
          <p className="mt-1 text-[11px] text-muted-foreground">{resolvedEmptyDescription}</p>
        </div>
      )}
    </GraphCanvas>
  );
}

export function SourceGraph({
  source,
  refreshKey,
  mode = "2d",
}: {
  source: Source;
  refreshKey: string;
  mode?: "2d" | "3d";
}) {
  const t = useTranslations("SourceGraph");
  const locale = useLocale();
  const { open } = useDetailPanel();
  const [graph, setGraph] = React.useState<SourceGraphResponse | null>(null);
  const [error, setError] = React.useState("");
  const [refreshVersion, setRefreshVersion] = React.useState(0);
  const [refreshing, setRefreshing] = React.useState(false);
  const [graphLimit, setGraphLimit] = React.useState(DEFAULT_GRAPH_LIMIT);
  const [graphLimitReady, setGraphLimitReady] = React.useState(false);
  const [filterQuery, setFilterQuery] = React.useState("");
  const [selectedEntityTypes, setSelectedEntityTypes] = React.useState<string[]>([]);

  React.useEffect(() => {
    const saved = Number(window.localStorage.getItem(GRAPH_LIMIT_STORAGE_KEY));
    if (GRAPH_LIMIT_OPTIONS.some((value) => value === saved)) setGraphLimit(saved);
    setGraphLimitReady(true);
  }, []);

  React.useEffect(() => {
    if (!graphLimitReady) return;
    let alive = true;
    const controller = new AbortController();
    setError("");
    setRefreshing(Boolean(graph));
    api
      .getSourceGraph(source.id, graphLimit, controller.signal)
      .then((response) => {
        if (!alive) return;
        setGraph(response);
      })
      .catch((reason) => {
        if (!alive) return;
        setError(reason instanceof ApiError ? reason.message : t("loadFailed"));
      })
      .finally(() => {
        if (alive) setRefreshing(false);
      });
    return () => {
      alive = false;
      controller.abort();
    };
    // graph 不能加入依赖；刷新时保留旧画面，避免闪烁。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source.id, refreshKey, refreshVersion, graphLimit, graphLimitReady, t]);

  if (!graph && !error) {
    return (
      <div className="h-full min-h-0 overflow-hidden rounded-lg border bg-card/40 p-4">
        <div className="flex h-full flex-col gap-3">
          <Skeleton className="h-9 w-64" />
          <Skeleton className="min-h-0 flex-1" />
        </div>
      </div>
    );
  }

  if (!graph && error) {
    return (
      <div className="flex h-full min-h-0 flex-col items-center justify-center rounded-lg border border-dashed bg-card/40 px-6 text-center">
        <Network className="size-8 text-muted-foreground/60" />
        <p className="mt-3 text-sm font-medium text-foreground">{t("sourceLoadFailed")}</p>
        <p className="mt-1 text-xs text-muted-foreground">{error}</p>
        <Button
          variant="outline"
          size="sm"
          className="mt-4"
          onClick={() => setRefreshVersion((value) => value + 1)}
        >
          <RefreshCw className="mr-1.5 size-3.5" />
          {t("retry")}
        </Button>
      </div>
    );
  }

  if (!graph) return null;

  const entityTypes = Array.from(new Set(graph.entities.map((entity) => entity.type || t("uncategorized"))))
    .sort((left, right) => left.localeCompare(right, locale));
  const normalizedQuery = filterQuery.trim().toLocaleLowerCase(locale);
  const filteredGraph = (() => {
    if (mode === "3d" || (!normalizedQuery && selectedEntityTypes.length === 0)) return graph;
    const selectedTypes = new Set(selectedEntityTypes);
    const allowedEntities = graph.entities.filter((entity) =>
      selectedTypes.size === 0 || selectedTypes.has(entity.type || t("uncategorized")),
    );
    const allowedEntityIds = new Set(allowedEntities.map((entity) => entity.id));
    const matchingEventIds = new Set(graph.events.filter((event) =>
      !normalizedQuery || [event.title, event.summary, event.category]
        .some((value) => value.toLocaleLowerCase(locale).includes(normalizedQuery)),
    ).map((event) => event.id));
    const matchingEntityIds = new Set(allowedEntities.filter((entity) =>
      !normalizedQuery || [entity.name, entity.type, entity.description]
        .some((value) => value.toLocaleLowerCase(locale).includes(normalizedQuery)),
    ).map((entity) => entity.id));
    const relations = graph.relations.filter((relation) => {
      if (relation.kind !== "mentions") return false;
      const eventId = relation.source_kind === "event" ? relation.source_id : relation.target_id;
      const entityId = relation.source_kind === "entity" ? relation.source_id : relation.target_id;
      if (!allowedEntityIds.has(entityId)) return false;
      return !normalizedQuery || matchingEventIds.has(eventId) || matchingEntityIds.has(entityId);
    });
    const relatedEventIds = new Set(relations.map((relation) =>
      relation.source_kind === "event" ? relation.source_id : relation.target_id,
    ));
    const relatedEntityIds = new Set(relations.map((relation) =>
      relation.source_kind === "entity" ? relation.source_id : relation.target_id,
    ));
    const visibleEventIds = normalizedQuery
      ? new Set([...matchingEventIds, ...relatedEventIds])
      : relatedEventIds;
    const visibleEntityIds = normalizedQuery
      ? new Set([...matchingEntityIds, ...relatedEntityIds])
      : relatedEntityIds;
    const events = graph.events.filter((event) => visibleEventIds.has(event.id));
    const visibleChunkIds = new Set(events.map((event) => event.chunk_id).filter(Boolean));
    const entities = allowedEntities.filter((entity) => visibleEntityIds.has(entity.id));
    return {
      ...graph,
      chunks: (graph.chunks ?? []).filter((chunk) => visibleChunkIds.has(chunk.id)),
      events,
      entities,
      relations,
      counts: {
        ...graph.counts,
        events: events.length,
        entities: entities.length,
        shown_events: events.length,
        shown_entities: entities.length,
        shown_relations: relations.length,
      },
      truncated: false,
    };
  })();

  const toggleEntityType = (type: string, checked: boolean) => {
    setSelectedEntityTypes((current) => checked
      ? Array.from(new Set([...current, type]))
      : current.filter((item) => item !== type));
  };

  const toolbarActions = (
    <>
      {mode === "2d" && (
        <>
          <div className="relative hidden sm:block">
            <Network className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={filterQuery}
              onChange={(event) => setFilterQuery(event.target.value)}
              placeholder={t("filterPlaceholder")}
              aria-label={t("filterAria")}
              className="h-8 w-44 bg-card/95 pl-8 pr-7 text-xs shadow-soft backdrop-blur-sm"
            />
            {filterQuery && (
              <button
                type="button"
                onClick={() => setFilterQuery("")}
                aria-label={t("clearFilter")}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              >
                <X className="size-3" />
              </button>
            )}
          </div>
          <details className="group relative">
            <summary className="flex h-8 cursor-pointer list-none items-center gap-1.5 rounded-md border bg-card/95 px-2.5 text-xs shadow-soft backdrop-blur-sm transition-colors hover:bg-muted [&::-webkit-details-marker]:hidden">
              <Filter className="size-3.5" />
              <span>{selectedEntityTypes.length ? t("selectedTypes", { count: selectedEntityTypes.length }) : t("entityTypes")}</span>
            </summary>
            <div className="absolute right-0 top-10 z-50 max-h-72 min-w-52 overflow-y-auto rounded-lg border bg-popover p-2 text-popover-foreground shadow-lift">
              <div className="flex items-center justify-between px-1 pb-2 text-[11px] font-medium text-muted-foreground">
                <span>{t("entityTypeRules")}</span>
                {selectedEntityTypes.length > 0 && (
                  <button type="button" className="text-primary hover:underline" onClick={() => setSelectedEntityTypes([])}>
                    {t("allTypes")}
                  </button>
                )}
              </div>
              <div className="space-y-0.5">
                {entityTypes.map((type) => (
                  <label key={type} className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-muted">
                    <Checkbox
                      checked={selectedEntityTypes.includes(type)}
                      onCheckedChange={(checked) => toggleEntityType(type, checked === true)}
                    />
                    <span className="min-w-0 flex-1 truncate">{type}</span>
                    <span className="text-[10px] tabular-nums text-muted-foreground">
                      {graph.entities.filter((entity) => (entity.type || t("uncategorized")) === type).length}
                    </span>
                  </label>
                ))}
              </div>
            </div>
          </details>
        </>
      )}
      <Select
        value={String(graphLimit)}
        onValueChange={(value) => {
          const next = Number(value);
          setGraphLimit(next);
          window.localStorage.setItem(GRAPH_LIMIT_STORAGE_KEY, String(next));
        }}
      >
        <SelectTrigger
          aria-label={t("limitAria")}
          title={t("limitTitle")}
          className="h-8 w-[6.5rem] bg-card/95 px-2 text-xs shadow-soft backdrop-blur-sm"
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {GRAPH_LIMIT_OPTIONS.map((value) => (
            <SelectItem key={value} value={String(value)}>
              {t("limitOption", { value: value.toLocaleString(locale) })}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Button
        variant="outline"
        size="icon"
        className="size-8 bg-card/95 shadow-soft backdrop-blur-sm"
        onClick={() => setRefreshVersion((value) => value + 1)}
        disabled={refreshing}
        aria-label={t("refresh")}
        title={t("refresh")}
      >
        <RefreshCw className={cn("size-3.5", refreshing && "animate-spin")} />
      </Button>
    </>
  );
  const onOpenEvent = (event: SourceGraphEvent) => {
    if (!event.chunk_id) return;
    open({
      kind: "chunk",
      sourceId: source.id,
      chunkId: event.chunk_id,
      heading: event.title,
      sourceName: source.name,
    });
  };
  const onOpenChunk = (chunk: SourceGraphChunk) => {
    open({
      kind: "chunk",
      sourceId: source.id,
      chunkId: chunk.id,
      heading: chunk.heading || t("untitledChunk"),
      sourceName: source.name,
    });
  };
  const sharedProps = {
    graph: filteredGraph,
    refreshKey: `${refreshKey}-${refreshVersion}-${graphLimit}-${normalizedQuery}-${selectedEntityTypes.join("|")}`,
    toolbarActions,
    onOpenEvent,
    onOpenChunk,
  };

  if (mode === "3d") return <OrbitalEventEntityGraph {...sharedProps} />;
  return <EventEntityGraph {...sharedProps} />;
}
