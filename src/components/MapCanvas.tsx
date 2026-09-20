import { useCallback, useEffect, useRef, useState, type MouseEvent, type PointerEvent } from "react";
import { ArrowsOut, Minus, Plus } from "@phosphor-icons/react";
import type { MapGap, MapPoint, MapRegion, Verdict } from "../types";
import { CLUSTER_META, CLUSTER_ORDER, REGION_META, clusterDetail, clusterMetaOf } from "../lib/regions";
import { VERDICT_META } from "../lib/verdicts";

interface MapCanvasProps {
  points: MapPoint[];
  regions: MapRegion[];
  gaps: MapGap[];
  placementPoint?: { x: number; y: number } | null;
  /** Region centres of a map that is still being built. */
  provisionalRegions?: { id: number; size?: number; x: number; y: number }[];
  /** True while the stream is still building this map, which changes what can be said about it. */
  building?: boolean;
}

const HOVER_RADIUS = 6;
const MIN_ZOOM = 1;
const MAX_ZOOM = 24;

/** Pan and zoom on top of the fitted view: `k` multiplies the fit, `tx`/`ty` shift it in pixels. */
interface Transform {
  k: number;
  tx: number;
  ty: number;
}
const IDENTITY: Transform = { k: 1, tx: 0, ty: 0 };
const clampZoom = (k: number) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, k));

/**
 * A colour per forming region, spaced around the wheel by the golden angle.
 *
 * It says which points the build currently groups together and nothing else: a
 * region earns one of the three labelled colours only once its studies have
 * been read, so these are identities, not verdicts.
 */
const formingColor = (id: number) => `hsl(${(id * 137.508) % 360} 52% 55%)`;

/** The extent of everything drawn, which only ever grows while a map streams in. */
interface Bounds {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
}

/** The fitted map-to-canvas transform, kept for hover hit-testing. */
interface View {
  width: number;
  height: number;
  toX: (x: number) => number;
  toY: (y: number) => number;
}

interface HoverBase {
  left: number;
  top: number;
  below: boolean;
}
interface PointHover extends HoverBase {
  kind: "point";
  point: MapPoint;
  region: MapRegion | undefined;
}
interface GapHover extends HoverBase {
  kind: "gap";
  gap: MapGap;
}
type Hover = PointHover | GapHover;

/** What a dashed line means, said once, next to the thing it describes. */
const GAP_CAPTION = "A stretch where the index holds almost no papers between these two literatures.";
const DISCOURAGED_CAPTION =
  "Almost no papers between these two literatures, and the work on either side reported nulls or never reported at all.";

/** Distance in canvas pixels from a point to a line segment, for hit-testing gaps. */
function segmentDistance(
  px: number,
  py: number,
  ax: number,
  ay: number,
  bx: number,
  by: number,
): number {
  const dx = bx - ax;
  const dy = by - ay;
  const length = dx * dx + dy * dy;
  const t = length ? Math.min(1, Math.max(0, ((px - ax) * dx + (py - ay) * dy) / length)) : 0;
  return Math.hypot(px - (ax + dx * t), py - (ay + dy * t));
}

export default function MapCanvas({ points, regions, gaps, placementPoint = null, provisionalRegions = [], building = false }: MapCanvasProps) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const viewRef = useRef<View | null>(null);
  const [hover, setHover] = useState<Hover | null>(null);
  const [transform, setTransform] = useState<Transform>(IDENTITY);
  const dragRef = useRef<{ pointerId: number; x: number; y: number; moved: boolean } | null>(null);
  const boundsRef = useRef<Bounds | null>(null);
  const movedRef = useRef(false);

  // The camera belongs to whoever is looking through it. A streaming map arrives
  // as hundreds of states, and reframing on each one would pull the ground from
  // under a reader who has zoomed in, so the view is only reset when the picture
  // itself is replaced — the finished map for the building one — and not then if
  // it has been moved by hand.
  useEffect(() => {
    boundsRef.current = null;
    if (!movedRef.current) setTransform(IDENTITY);
  }, [building]);

  useEffect(() => {
    const wrap = wrapRef.current;
    const canvas = canvasRef.current;
    if (!wrap || !canvas) return;

    const draw = () => {
      const width = wrap.clientWidth;
      const height = wrap.clientHeight;
      if (!width || !height) return;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);

      let minX = Infinity;
      let maxX = -Infinity;
      let minY = Infinity;
      let maxY = -Infinity;
      const include = (x: number, y: number) => {
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      };
      for (const point of points) include(point.x, point.y);
      for (const region of regions) include(region.x, region.y);
      for (const region of provisionalRegions) include(region.x, region.y);
      if (placementPoint) include(placementPoint.x, placementPoint.y);
      if (!isFinite(minX)) {
        viewRef.current = null;
        return;
      }
      // Widened, never narrowed: a fit recomputed from scratch on every state
      // would breathe in and out as the stream fills the plane in.
      const held = boundsRef.current;
      if (held) {
        minX = Math.min(minX, held.minX);
        maxX = Math.max(maxX, held.maxX);
        minY = Math.min(minY, held.minY);
        maxY = Math.max(maxY, held.maxY);
      }
      boundsRef.current = { minX, maxX, minY, maxY };
      const pad = 28;
      // One scale for both axes: stretching would invent distances the PCA did not find.
      const scale = Math.min(
        (width - pad * 2) / Math.max(maxX - minX, 1e-9),
        (height - pad * 2) / Math.max(maxY - minY, 1e-9),
      );
      const midX = (minX + maxX) / 2;
      const midY = (minY + maxY) / 2;
      const { k, tx, ty } = transform;
      const view: View = {
        width,
        height,
        toX: (x) => (width / 2 + (x - midX) * scale) * k + tx,
        toY: (y) => (height / 2 - (y - midY) * scale) * k + ty,
      };
      viewRef.current = view;

      // Resolved per draw so a theme change repaints in the new palette.
      const styles = getComputedStyle(document.documentElement);
      const color = (name: string, fallback: string) =>
        styles.getPropertyValue(name).trim() || fallback;
      const ink = color("--ink", "#16161a");
      const faint = color("--line-strong", "#c6c6ce");
      const accent = color("--accent", "#2a55c2");
      const failed = color("--v-failed", "#c0463f");
      const grey = color("--ink-3", "#75757e");
      const regionColor = new Map<number, string>();
      const regionById = new Map<number, MapRegion>();
      for (const region of regions) {
        regionColor.set(region.id, color(clusterMetaOf(region.label).colorVar, grey));
        regionById.set(region.id, region);
      }

      ctx.setLineDash([4, 4]);
      for (const gap of gaps) {
        const left = regionById.get(gap.regions[0]);
        const right = regionById.get(gap.regions[1]);
        if (!left || !right) continue;
        ctx.strokeStyle = gap.discouraged ? failed : faint;
        ctx.lineWidth = gap.discouraged ? 2 : 1;
        ctx.beginPath();
        ctx.moveTo(view.toX(left.x), view.toY(left.y));
        ctx.lineTo(view.toX(right.x), view.toY(right.y));
        ctx.stroke();
      }
      ctx.setLineDash([]);

      // Marks grow with the zoom, but far more slowly than the distances between them.
      const mark = Math.min(3, Math.pow(k, 0.35));

      // While the map is building, each point is drawn in the colour of the
      // region it currently belongs to and tied to that region's centre, so the
      // clustering is visible as it happens instead of a grey field that turns
      // into an answer at the end.
      const centre = new Map<number, { x: number; y: number }>();
      for (const region of provisionalRegions) centre.set(region.id, region);
      if (building) {
        ctx.lineWidth = 0.6;
        for (const point of points) {
          const home = centre.get(point.region);
          if (!home) continue;
          ctx.strokeStyle = formingColor(point.region);
          ctx.globalAlpha = 0.16;
          ctx.beginPath();
          ctx.moveTo(view.toX(point.x), view.toY(point.y));
          ctx.lineTo(view.toX(home.x), view.toY(home.y));
          ctx.stroke();
        }
        ctx.globalAlpha = 1;
      }

      for (const point of points) {
        ctx.fillStyle = building
          ? formingColor(point.region)
          : regionColor.get(point.region) ?? grey;
        ctx.beginPath();
        ctx.arc(view.toX(point.x), view.toY(point.y), 1.3 * mark, 0, Math.PI * 2);
        ctx.fill();
      }

      for (const region of regions) {
        ctx.beginPath();
        ctx.arc(view.toX(region.x), view.toY(region.y), 4.5 * mark, 0, Math.PI * 2);
        ctx.fillStyle = regionColor.get(region.id) ?? grey;
        ctx.fill();
        ctx.lineWidth = 1.4;
        ctx.strokeStyle = ink;
        ctx.stroke();
      }

      for (const region of provisionalRegions) {
        ctx.beginPath();
        ctx.arc(view.toX(region.x), view.toY(region.y), 4.5 * mark, 0, Math.PI * 2);
        ctx.fillStyle = formingColor(region.id);
        ctx.fill();
        ctx.lineWidth = 1.4;
        ctx.strokeStyle = ink;
        ctx.stroke();
      }

      if (placementPoint) {
        const x = view.toX(placementPoint.x);
        const y = view.toY(placementPoint.y);
        ctx.strokeStyle = accent;
        ctx.fillStyle = accent;
        ctx.lineWidth = 1.8;
        ctx.beginPath();
        ctx.arc(x, y, 7, 0, Math.PI * 2);
        ctx.stroke();
        ctx.beginPath();
        for (const [dx, dy] of [[-1, 0], [1, 0], [0, -1], [0, 1]] as const) {
          ctx.moveTo(x + dx * 9, y + dy * 9);
          ctx.lineTo(x + dx * 13, y + dy * 13);
        }
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(x, y, 1.8, 0, Math.PI * 2);
        ctx.fill();
      }
    };

    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(wrap);
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", draw);
    return () => {
      observer.disconnect();
      media.removeEventListener("change", draw);
    };
  }, [points, regions, gaps, placementPoint, provisionalRegions, transform, building]);

  /** Zoom about a point in canvas pixels, so whatever is under the cursor stays under it. */
  const zoomAt = useCallback((factor: number, px: number, py: number) => {
    movedRef.current = true;
    setTransform(({ k, tx, ty }) => {
      const next = clampZoom(k * factor);
      const ratio = next / k;
      return { k: next, tx: px - (px - tx) * ratio, ty: py - (py - ty) * ratio };
    });
  }, []);

  // Wheel zoom has to be non-passive to keep the page from scrolling under the map.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = canvas.getBoundingClientRect();
      zoomAt(Math.exp(-event.deltaY * 0.002), event.clientX - rect.left, event.clientY - rect.top);
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  }, [zoomAt]);

  const zoomCentre = (factor: number) => {
    const canvas = canvasRef.current;
    if (canvas) zoomAt(factor, canvas.clientWidth / 2, canvas.clientHeight / 2);
  };

  function handlePointerDown(event: PointerEvent<HTMLCanvasElement>) {
    if (event.button !== 0) return;
    dragRef.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, moved: false };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function handlePointerUp(event: PointerEvent<HTMLCanvasElement>) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    dragRef.current = null;
    event.currentTarget.releasePointerCapture(event.pointerId);
  }

  function handleMove(event: MouseEvent<HTMLCanvasElement>) {
    const view = viewRef.current;
    const canvas = canvasRef.current;
    if (!view || !canvas) return;
    const drag = dragRef.current;
    if (drag) {
      const dx = event.clientX - drag.x;
      const dy = event.clientY - drag.y;
      drag.x = event.clientX;
      drag.y = event.clientY;
      drag.moved = drag.moved || Math.abs(dx) + Math.abs(dy) > 1;
      movedRef.current = movedRef.current || drag.moved;
      setTransform((current) => ({ ...current, tx: current.tx + dx, ty: current.ty + dy }));
      setHover(null);
      return;
    }
    const rect = canvas.getBoundingClientRect();
    const mx = event.clientX - rect.left;
    const my = event.clientY - rect.top;
    let best: MapPoint | null = null;
    let bestDist = HOVER_RADIUS * HOVER_RADIUS;
    for (const point of points) {
      const dx = view.toX(point.x) - mx;
      const dy = view.toY(point.y) - my;
      const dist = dx * dx + dy * dy;
      if (dist < bestDist) {
        bestDist = dist;
        best = point;
      }
    }
    if (best) {
      const x = view.toX(best.x);
      const y = view.toY(best.y);
      setHover({
        kind: "point",
        left: Math.min(Math.max(x, 96), rect.width - 96),
        top: y,
        below: y < 70,
        point: best,
        region: regions.find((region) => region.id === best.region),
      });
      return;
    }
    // Nothing under the cursor: the dashed bands explain themselves on hover, so the
    // legend does not have to carry a sentence about them.
    const regionById = new Map(regions.map((region) => [region.id, region]));
    let gapHit: MapGap | null = null;
    let gapDist = HOVER_RADIUS;
    let gapX = 0;
    let gapY = 0;
    for (const gap of gaps) {
      const left = regionById.get(gap.regions[0]);
      const right = regionById.get(gap.regions[1]);
      if (!left || !right) continue;
      const ax = view.toX(left.x);
      const ay = view.toY(left.y);
      const bx = view.toX(right.x);
      const by = view.toY(right.y);
      const dist = segmentDistance(mx, my, ax, ay, bx, by);
      if (dist < gapDist) {
        gapDist = dist;
        gapHit = gap;
        gapX = (ax + bx) / 2;
        gapY = (ay + by) / 2;
      }
    }
    if (!gapHit) {
      setHover(null);
      return;
    }
    setHover({
      kind: "gap",
      left: Math.min(Math.max(gapX, 110), rect.width - 110),
      top: gapY,
      below: gapY < 90,
      gap: gapHit,
    });
  }

  const presentLabels = regions.map((region) => region.label);
  const presentClusters = CLUSTER_ORDER.filter((cluster) => clusterDetail(cluster, presentLabels));
  const ariaLabel =
    `Scatter map of ${points.length} drawn studies in ${regions.length} regions` +
    (gaps.length
      ? ", with dashed lines marking stretches between two neighbouring literatures where the index holds almost no papers"
      : "") +
    ". Each region is described in the list below.";

  return (
    <figure>
      <div
        ref={wrapRef}
        className="relative h-[340px] overflow-hidden rounded-panel border border-line bg-surface sm:h-[420px]"
      >
        <canvas
          ref={canvasRef}
          role="img"
          aria-label={ariaLabel}
          className="block h-full w-full touch-none"
          style={{ cursor: dragRef.current ? "grabbing" : hover?.kind === "point" ? "pointer" : "grab" }}
          onMouseMove={handleMove}
          onMouseLeave={() => setHover(null)}
          onPointerDown={handlePointerDown}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerUp}
          onDoubleClick={() => { movedRef.current = false; setTransform(IDENTITY); }}
        />
        <div className="absolute right-2 top-2 flex flex-col gap-1">
          {[
            { label: "Zoom in", icon: <Plus size={14} weight="bold" />, run: () => zoomCentre(1.4), off: transform.k >= MAX_ZOOM },
            { label: "Zoom out", icon: <Minus size={14} weight="bold" />, run: () => zoomCentre(1 / 1.4), off: transform.k <= MIN_ZOOM },
            { label: "Reset the view", icon: <ArrowsOut size={14} weight="bold" />, run: () => { movedRef.current = false; setTransform(IDENTITY); }, off: transform.k === 1 && transform.tx === 0 && transform.ty === 0 },
          ].map(({ label, icon, run, off }) => (
            <button
              key={label}
              type="button"
              onClick={run}
              disabled={off}
              aria-label={label}
              title={label}
              className="rounded-control border border-line bg-surface p-1.5 text-ink-2 transition-colors hover:border-line-strong hover:text-ink disabled:opacity-40"
            >
              {icon}
            </button>
          ))}
        </div>
        {hover && (
          <div
            className={`pointer-events-none absolute z-10 rounded-control border border-line bg-surface px-2.5 py-1.5 shadow-panel ${hover.kind === "gap" ? "w-60" : "w-52"}`}
            style={{
              left: hover.left,
              top: hover.top,
              transform: hover.below
                ? "translate(-50%, 14px)"
                : "translate(-50%, calc(-100% - 14px))",
            }}
          >
            {hover.kind === "point" ? (
              <>
                <p className="text-xs font-medium leading-snug text-ink">{hover.point.title}</p>
                <p className="mt-0.5 text-xs text-ink-3">
                  {hover.point.year ?? "year unknown"} ·{" "}
                  {VERDICT_META[hover.point.bucket as Verdict]?.label ?? hover.point.bucket}
                </p>
                {hover.region && (
                  <p className="mt-0.5 text-xs text-ink-3">
                    {clusterMetaOf(hover.region.label).label} ·{" "}
                    {REGION_META[hover.region.label].label.toLowerCase()}
                  </p>
                )}
              </>
            ) : (
              <>
                <p className="text-xs font-medium leading-snug text-ink">
                  {hover.gap.parentLabels
                    .map((label) => REGION_META[label]?.label ?? label)
                    .join(" ↔ ")}
                </p>
                <p className="mt-0.5 text-xs leading-snug text-ink-3">
                  {hover.gap.discouraged ? DISCOURAGED_CAPTION : GAP_CAPTION}
                </p>
              </>
            )}
          </div>
        )}
      </div>
      {/*
        Three separate things, kept apart: what a dot means, what a line or ring
        means, and how to move the map. The dashed band is named here and
        explained where it is drawn, on hover.
      */}
      <figcaption className="mt-2.5 space-y-1.5 text-xs text-ink-3">
        {building && (
          <p className="text-ink-2">
            Regions as they form: a colour marks the studies the build currently groups together,
            and each dot is tied to the centre of its region. The three labelled groups appear once
            the studies behind each region have been read.
          </p>
        )}
        {presentClusters.length > 0 && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="shrink-0 text-ink-2">Clusters:</span>
            {presentClusters.map((cluster) => (
              <span
                key={cluster}
                className="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap"
                title={`${CLUSTER_META[cluster].description} In this map: ${clusterDetail(cluster, presentLabels)}.`}
              >
                <span
                  className="h-2 w-2 rounded-full"
                  style={{ backgroundColor: `var(${CLUSTER_META[cluster].colorVar})` }}
                  aria-hidden
                />
                {CLUSTER_META[cluster].label}
              </span>
            ))}
          </div>
        )}
        {(gaps.length > 0 || placementPoint) && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="shrink-0 text-ink-2">Markers:</span>
            {gaps.length > 0 && (
              <span className="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap" title={GAP_CAPTION}>
                <span
                  className="w-3.5"
                  style={{ borderTop: "2px dashed var(--line-strong)" }}
                  aria-hidden
                />
                Sparse band
              </span>
            )}
            {gaps.some((gap) => gap.discouraged) && (
              <span className="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap" title={DISCOURAGED_CAPTION}>
                <span
                  className="w-3.5"
                  style={{ borderTop: "2px dashed var(--v-failed)" }}
                  aria-hidden
                />
                Nulls either side
              </span>
            )}
            {placementPoint && (
              <span className="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap" title="Where your idea sits among the clustered studies.">
                <span
                  className="h-2.5 w-2.5 rounded-full"
                  style={{ border: "2px solid var(--accent)" }}
                  aria-hidden
                />
                Your idea
              </span>
            )}
          </div>
        )}
        <p className="text-ink-3/70">
          Drag to pan · scroll to zoom · double-click to reset{building ? " · the view stays where you put it while the map builds" : ""}
          {transform.k > 1 ? ` · showing ${transform.k.toFixed(1)}×` : ""}
          {gaps.length > 0 ? " · hover a dashed band to see what it means" : ""}
        </p>
      </figcaption>
    </figure>
  );
}
