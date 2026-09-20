import { useCallback, useEffect, useRef, useState, type MouseEvent, type PointerEvent } from "react";
import { ArrowClockwise, ArrowsOut, Minus, Plus } from "@phosphor-icons/react";
import type { MapEdge, MapPoint, Verdict } from "../types";
import { clusterHue, clusterLabels, countClusters, pruneEdges } from "../lib/clusters";
import { ForceLayout } from "../lib/forceLayout";
import { VERDICT_META } from "../lib/verdicts";

interface MapCanvasProps {
  points: MapPoint[];
  /** Similarity edges over `points` by position; only pairs above the cosine floor. */
  edges: MapEdge[];
  placementPoint?: { x: number; y: number } | null;
  /** True while the stream is still building this map, which changes what can be said about it. */
  building?: boolean;
}

const HOVER_RADIUS = 8;
/** Links kept per paper. Everything above the server's floor is a hairball; its strongest ties are a graph. */
const LINKS_PER_PAPER = 6;
/** Below the fit, so the whole graph can be pushed back and read as a shape. */
const MIN_ZOOM = 0.15;
const MAX_ZOOM = 24;
/** How the replay lets the papers back in: this many arrivals, this far apart. */
const REPLAY_STEPS = 24;
const REPLAY_INTERVAL = 90;
/** The layout id of the reader's own question, which is a node like the papers. */
const QUESTION_ID = "\u0000question";

/** Pan and zoom on top of the fitted view: `k` multiplies the fit, `tx`/`ty` shift it in pixels. */
interface Transform {
  k: number;
  tx: number;
  ty: number;
}
const IDENTITY: Transform = { k: 1, tx: 0, ty: 0 };
const clampZoom = (k: number) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, k));

/**
 * One color per cluster of linked papers, spaced around the wheel by the golden
 * angle. `weight` runs 0 to 1 with how connected the paper is: the hubs of a
 * topic read darker than its leaves, so density is legible before any label is.
 */
const clusterColor = (cluster: number, weight = 0.5) =>
  `hsl(${clusterHue(cluster)} ${54 + weight * 12}% ${60 - weight * 16}%)`;

/** The extent of everything drawn, which only ever grows while a map streams in. */
interface Bounds {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
}

/** The fitted plane-to-canvas transform at the base zoom, before pan and zoom. */
interface Fit {
  scale: number;
  midX: number;
  midY: number;
}

interface Hover {
  left: number;
  top: number;
  below: boolean;
  point: MapPoint;
  degree: number;
  clustered: boolean;
}

export default function MapCanvas({ points, edges, placementPoint = null, building = false }: MapCanvasProps) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const layoutRef = useRef<ForceLayout | null>(null);
  if (!layoutRef.current) layoutRef.current = new ForceLayout();
  const [hover, setHover] = useState<Hover | null>(null);
  const hoverRef = useRef(hover);
  hoverRef.current = hover;
  const [transform, setTransform] = useState<Transform>(IDENTITY);
  const transformRef = useRef(transform);
  transformRef.current = transform;
  const fitRef = useRef<Fit | null>(null);
  const boundsRef = useRef<Bounds | null>(null);
  const movedRef = useRef(false);
  const dragRef = useRef<{ pointerId: number; x: number; y: number; moved: boolean; node: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const dirtyRef = useRef(true);
  const [clusterCount, setClusterCount] = useState(0);
  // While a replay runs this is how many papers have arrived so far; null is
  // the normal state, where every paper the server sent is on the map.
  const [revealed, setRevealed] = useState<number | null>(null);
  const latest = useRef<{ points: MapPoint[]; edges: MapEdge[]; placementPoint: { x: number; y: number } | null; building: boolean; clusters: Int32Array }>({ points, edges, placementPoint, building, clusters: new Int32Array(0) });

  // The simulation is fed, never rebuilt: papers the layout has seen keep the
  // place the forces gave them, newcomers enter at their projected coordinates,
  // and new links reheat rather than restart. Positions are by paper id, so a
  // frame that reorders or drops papers cannot move the ones that stay.
  useEffect(() => {
    const layout = layoutRef.current!;
    // A replay shows the same map arriving a few papers at a time: the ones
    // that have not arrived yet are simply not in the layout, so they enter at
    // their projected coordinates and are drawn into place by the same forces.
    const shown = revealed === null ? points : points.slice(0, revealed);
    const shownEdges = revealed === null
      ? edges
      : edges.filter(([a, b]) => a < shown.length && b < shown.length);
    const ids = new Set<string>();
    const inputs = shown.map((point) => {
      ids.add(point.id);
      return { id: point.id, x: point.x, y: point.y };
    });
    if (placementPoint) {
      ids.add(QUESTION_ID);
      inputs.push({ id: QUESTION_ID, x: placementPoint.x, y: placementPoint.y });
    }
    const emptied = layout.size > 0 && ids.size === 0;
    layout.retain(ids);
    layout.update(inputs);
    const strongest = pruneEdges(shown.length, shownEdges, LINKS_PER_PAPER);
    layout.unlink();
    layout.link(strongest.flatMap(([a, b, cosine]) =>
      shown[a] && shown[b] ? [[shown[a].id, shown[b].id, cosine] as const] : []));
    const clusters = clusterLabels(shown.length, strongest);
    latest.current = { points: shown, edges: strongest, placementPoint, building, clusters };
    setClusterCount(countClusters(clusters));
    if (emptied) {
      // A new question, a new picture: only then does the camera go home.
      boundsRef.current = null;
      movedRef.current = false;
      setTransform(IDENTITY);
    }
    dirtyRef.current = true;
  }, [points, edges, placementPoint, building, revealed]);

  // The replay itself: papers arrive in equal batches until they are all back,
  // then the map returns to following the server's set.
  useEffect(() => {
    if (revealed === null) return;
    if (revealed >= points.length) {
      setRevealed(null);
      return;
    }
    const batch = Math.max(1, Math.ceil(points.length / REPLAY_STEPS));
    const timer = window.setTimeout(() => setRevealed((seen) => (seen === null ? null : seen + batch)), REPLAY_INTERVAL);
    return () => window.clearTimeout(timer);
  }, [revealed, points.length]);

  useEffect(() => {
    dirtyRef.current = true;
  }, [transform, hover]);

  useEffect(() => {
    const wrap = wrapRef.current;
    const canvas = canvasRef.current;
    if (!wrap || !canvas) return;
    const layout = layoutRef.current!;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

    // Resolved through an element rather than read off the custom property:
    // the palette is written as `light-dark(...)`, which a custom property
    // hands back verbatim. A canvas rejects that and silently keeps the
    // previous fill, which is how the whole map once came out black. Computing
    // `color` on a probe picks the branch for the active scheme.
    let palette: { ink: string; faint: string; accent: string; grey: string; surface: string } | null = null;
    const resolvePalette = () => {
      const probe = document.createElement("span");
      probe.style.display = "none";
      wrap.appendChild(probe);
      const color = (name: string, fallback: string) => {
        probe.style.color = fallback;
        probe.style.color = `var(${name}, ${fallback})`;
        return getComputedStyle(probe).color || fallback;
      };
      palette = {
        ink: color("--ink", "#16161a"),
        faint: color("--line-strong", "#c6c6ce"),
        accent: color("--accent", "#2a55c2"),
        grey: color("--ink-3", "#75757e"),
        surface: color("--surface", "#ffffff"),
      };
      probe.remove();
      dirtyRef.current = true;
    };

    /** The fit at the base zoom: one scale for both axes, over bounds that only widen. */
    const fit = (width: number, height: number): Fit | null => {
      let minX = Infinity;
      let maxX = -Infinity;
      let minY = Infinity;
      let maxY = -Infinity;
      for (let i = 0; i < layout.size; i += 1) {
        const x = layout.xAt(i);
        const y = layout.yAt(i);
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      }
      if (!isFinite(minX)) return null;
      // Widened, never narrowed: a fit recomputed from scratch on every frame
      // would breathe in and out as the stream fills the plane in and the
      // simulation spreads it.
      const held = boundsRef.current;
      if (held) {
        minX = Math.min(minX, held.minX);
        maxX = Math.max(maxX, held.maxX);
        minY = Math.min(minY, held.minY);
        maxY = Math.max(maxY, held.maxY);
      }
      if (!held || held.minX !== minX || held.maxX !== maxX || held.minY !== minY || held.maxY !== maxY) {
        boundsRef.current = { minX, maxX, minY, maxY };
      }
      const pad = 28;
      const scale = Math.min(
        (width - pad * 2) / Math.max(maxX - minX, 1e-9),
        (height - pad * 2) / Math.max(maxY - minY, 1e-9),
      );
      return { scale, midX: (minX + maxX) / 2, midY: (minY + maxY) / 2 };
    };

    const draw = () => {
      const width = wrap.clientWidth;
      const height = wrap.clientHeight;
      if (!width || !height) return;
      if (!palette) resolvePalette();
      const colors = palette!;
      const dpr = window.devicePixelRatio || 1;
      if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
        canvas.width = Math.round(width * dpr);
        canvas.height = Math.round(height * dpr);
      }
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);

      // Once the reader has panned, zoomed, or grabbed a paper, the base fit is
      // theirs: the simulation spreading past the held bounds must not rescale
      // the view under their pointer.
      const held = fitRef.current !== null && (movedRef.current || dragRef.current !== null);
      const current = held ? fitRef.current : fit(width, height);
      fitRef.current = current;
      if (!current) return;
      const { scale, midX, midY } = current;
      const { k, tx, ty } = transformRef.current;
      const toX = (x: number) => (width / 2 + (x - midX) * scale) * k + tx;
      const toY = (y: number) => (height / 2 - (y - midY) * scale) * k + ty;
      // Marks grow with the zoom, but far more slowly than the distances between them.
      const mark = Math.min(3, Math.pow(k, 0.35));
      const { points: drawn, clusters, placementPoint: question } = latest.current;

      // Which topic each node belongs to, by layout index, so a line can take
      // the color of the topic it holds together.
      const topic = new Int32Array(layout.size).fill(-1);
      for (let index = 0; index < drawn.length; index += 1) {
        const i = layout.indexOf(drawn[index].id);
        if (i >= 0) topic[i] = clusters[index] ?? -1;
      }

      // Lines first, under the dots: a line is the reason two papers sit together.
      ctx.lineCap = "round";
      layout.eachLink((i, j, cosine) => {
        const share = Math.max(0, (cosine - layout.options.linkFloor) / (1 - layout.options.linkFloor));
        const within = topic[i] >= 0 && topic[i] === topic[j];
        ctx.strokeStyle = within ? clusterColor(topic[i], 0.35) : colors.faint;
        ctx.globalAlpha = within ? 0.2 + share * 0.4 : 0.14 + share * 0.2;
        ctx.lineWidth = (0.5 + share * 0.9) * Math.min(mark, 1.6);
        ctx.beginPath();
        ctx.moveTo(toX(layout.xAt(i)), toY(layout.yAt(i)));
        ctx.lineTo(toX(layout.xAt(j)), toY(layout.yAt(j)));
        ctx.stroke();
      });
      ctx.globalAlpha = 1;

      // One dot per paper, sized by how many papers it is linked to and colored
      // by the cluster those links put it in. A paper with no links is grey.
      const { pointRadius, maxRadius } = layout.options;
      for (let index = 0; index < drawn.length; index += 1) {
        const i = layout.indexOf(drawn[index].id);
        if (i < 0) continue;
        const cluster = topic[i];
        const radius = layout.radiusAt(i);
        const weight = Math.min(1, Math.max(0, (radius - pointRadius) / Math.max(maxRadius - pointRadius, 1e-9)));
        ctx.fillStyle = cluster >= 0 ? clusterColor(cluster, weight) : colors.grey;
        ctx.globalAlpha = cluster >= 0 ? 1 : 0.5;
        ctx.beginPath();
        ctx.arc(toX(layout.xAt(i)), toY(layout.yAt(i)), radius * mark, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;

      // The question is a node like the papers, but the only blue one and the
      // only one with a halo, so the eye finds it without hunting.
      if (question) {
        const i = layout.indexOf(QUESTION_ID);
        if (i >= 0) {
          const x = toX(layout.xAt(i));
          const y = toY(layout.yAt(i));
          const radius = (layout.options.maxRadius + 2) * mark;
          ctx.globalAlpha = 0.22;
          ctx.fillStyle = colors.accent;
          ctx.beginPath();
          ctx.arc(x, y, radius * 2.6, 0, Math.PI * 2);
          ctx.fill();
          ctx.globalAlpha = 1;
          ctx.fillStyle = colors.accent;
          ctx.strokeStyle = colors.surface;
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.arc(x, y, radius, 0, Math.PI * 2);
          ctx.fill();
          ctx.stroke();
        }
      }

      const hovered = hoverRef.current;
      if (hovered) {
        const i = layout.indexOf(hovered.point.id);
        if (i >= 0) {
          ctx.strokeStyle = colors.ink;
          ctx.lineWidth = 1.5;
          ctx.beginPath();
          ctx.arc(toX(layout.xAt(i)), toY(layout.yAt(i)), layout.radiusAt(i) * mark + 2, 0, Math.PI * 2);
          ctx.stroke();
        }
      }
    };

    // One loop for physics and paint. The simulation runs while it has energy;
    // the canvas is repainted only when something moved, changed, or was asked.
    let frame = 0;
    const step = () => {
      frame = requestAnimationFrame(step);
      const width = wrap.clientWidth;
      const height = wrap.clientHeight;
      if (!width || !height) return;
      const current = fitRef.current ?? fit(width, height);
      const pixelsPerUnit = current ? current.scale : 1;
      if (!layout.settled) {
        if (reducedMotion.matches) layout.settle(pixelsPerUnit, 30);
        else layout.tick(pixelsPerUnit);
        dirtyRef.current = true;
      }
      if (dirtyRef.current) {
        dirtyRef.current = false;
        draw();
      }
    };
    frame = requestAnimationFrame(step);

    const observer = new ResizeObserver(() => {
      dirtyRef.current = true;
    });
    observer.observe(wrap);
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", resolvePalette);
    // The top-bar toggle sets data-theme rather than changing the OS setting,
    // and the canvas holds pixels, not styles, so it has to be told to repaint.
    const theme = new MutationObserver(resolvePalette);
    theme.observe(document.documentElement, { attributeFilter: ["data-theme"] });
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      theme.disconnect();
      media.removeEventListener("change", resolvePalette);
    };
  }, []);

  /** Canvas pixels to plane coordinates, through the current fit, pan, and zoom. */
  const toPlane = useCallback((px: number, py: number): { x: number; y: number; unit: number } | null => {
    const current = fitRef.current;
    const canvas = canvasRef.current;
    if (!current || !canvas) return null;
    const { k, tx, ty } = transformRef.current;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    const unit = 1 / (current.scale * k);
    return {
      x: current.midX + ((px - tx) / k - width / 2) * unit * k,
      y: current.midY - ((py - ty) / k - height / 2) * unit * k,
      unit,
    };
  }, []);

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

  const zoomCenter = (factor: number) => {
    const canvas = canvasRef.current;
    if (canvas) zoomAt(factor, canvas.clientWidth / 2, canvas.clientHeight / 2);
  };

  /**
   * Runs the layout to rest at once, so a click ends the drifting instead of
   * waiting it out. Bounded, because settling a full map is not free.
   */
  const snap = useCallback(() => {
    const layout = layoutRef.current!;
    layout.settle(fitRef.current?.scale ?? 1, 300);
    dirtyRef.current = true;
  }, []);

  /** Empties the map and lets the same papers arrive again, a few at a time. */
  const replay = useCallback(() => {
    if (!points.length) return;
    setHover(null);
    setRevealed(1);
  }, [points.length]);

  /** The layout node under a canvas pixel, or -1. */
  const nodeAt = (px: number, py: number): number => {
    const plane = toPlane(px, py);
    if (!plane) return -1;
    return layoutRef.current!.nearest(plane.x, plane.y, HOVER_RADIUS * plane.unit);
  };

  function handlePointerDown(event: PointerEvent<HTMLCanvasElement>) {
    if (event.button !== 0) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const node = nodeAt(event.clientX - rect.left, event.clientY - rect.top);
    // A paper under the pointer is grabbed and held there while the rest of the
    // graph rearranges around it; empty plane pans.
    if (node >= 0) {
      const plane = toPlane(event.clientX - rect.left, event.clientY - rect.top)!;
      layoutRef.current!.pin(node, plane.x, plane.y);
    }
    dragRef.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, moved: false, node };
    setDragging(true);
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function handlePointerUp(event: PointerEvent<HTMLCanvasElement>) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (drag.node >= 0) layoutRef.current!.release(drag.node);
    dragRef.current = null;
    setDragging(false);
    event.currentTarget.releasePointerCapture(event.pointerId);
    // A click that went nowhere is a request for the picture to stop moving.
    if (!drag.moved) snap();
  }

  function handleMove(event: MouseEvent<HTMLCanvasElement>) {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const mx = event.clientX - rect.left;
    const my = event.clientY - rect.top;
    const drag = dragRef.current;
    if (drag) {
      const dx = event.clientX - drag.x;
      const dy = event.clientY - drag.y;
      drag.x = event.clientX;
      drag.y = event.clientY;
      drag.moved = drag.moved || Math.abs(dx) + Math.abs(dy) > 1;
      movedRef.current = movedRef.current || drag.moved;
      if (drag.node >= 0) {
        const plane = toPlane(mx, my);
        if (plane) layoutRef.current!.pin(drag.node, plane.x, plane.y);
        dirtyRef.current = true;
      } else {
        setTransform((current) => ({ ...current, tx: current.tx + dx, ty: current.ty + dy }));
      }
      setHover(null);
      return;
    }
    const layout = layoutRef.current!;
    const node = nodeAt(mx, my);
    const id = node >= 0 ? layout.idAt(node) : null;
    const index = id === null ? -1 : latest.current.points.findIndex((point) => point.id === id);
    if (index < 0) {
      if (hover) setHover(null);
      return;
    }
    const point = latest.current.points[index];
    if (hover?.point.id === point.id) return;
    const fitNow = fitRef.current!;
    const { k, tx, ty } = transformRef.current;
    const x = (rect.width / 2 + (layout.xAt(node) - fitNow.midX) * fitNow.scale) * k + tx;
    const y = (rect.height / 2 - (layout.yAt(node) - fitNow.midY) * fitNow.scale) * k + ty;
    setHover({
      left: Math.min(Math.max(x, 96), rect.width - 96),
      top: y,
      below: y < 70,
      point,
      degree: layout.degreeAt(node),
      clustered: (latest.current.clusters[index] ?? -1) >= 0,
    });
  }

  const ariaLabel =
    `Graph of ${points.length} studies, one dot each, joined by ${edges.length} lines where two studies are ` +
    `closely similar, forming ${clusterCount} clusters.`;

  return (
    <figure>
      <div
        ref={wrapRef}
        className="relative h-[520px] overflow-hidden rounded-panel border border-line bg-surface sm:h-[720px]"
      >
        <canvas
          ref={canvasRef}
          role="img"
          aria-label={ariaLabel}
          className="block h-full w-full touch-none"
          style={{ cursor: dragging ? "grabbing" : hover ? "pointer" : "grab" }}
          onMouseMove={handleMove}
          onMouseLeave={() => setHover(null)}
          onPointerDown={handlePointerDown}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerUp}
          onDoubleClick={() => { movedRef.current = false; setTransform(IDENTITY); }}
        />
        <div className="absolute right-2 top-2 flex flex-col gap-1">
          {[
            { label: "Zoom in", icon: <Plus size={14} weight="bold" />, run: () => zoomCenter(1.4), off: transform.k >= MAX_ZOOM },
            { label: "Zoom out", icon: <Minus size={14} weight="bold" />, run: () => zoomCenter(1 / 1.4), off: transform.k <= MIN_ZOOM },
            { label: "Reset the view", icon: <ArrowsOut size={14} weight="bold" />, run: () => { movedRef.current = false; setTransform(IDENTITY); }, off: transform.k === 1 && transform.tx === 0 && transform.ty === 0 },
            { label: "Replay the papers arriving", icon: <ArrowClockwise size={14} weight="bold" />, run: replay, off: revealed !== null || points.length === 0 },
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
            className="pointer-events-none absolute z-10 w-52 rounded-control border border-line bg-surface px-2.5 py-1.5 shadow-panel"
            style={{
              left: hover.left,
              top: hover.top,
              transform: hover.below
                ? "translate(-50%, 14px)"
                : "translate(-50%, calc(-100% - 14px))",
            }}
          >
            <p className="text-xs font-medium leading-snug text-ink">{hover.point.title}</p>
            <p className="mt-0.5 text-xs text-ink-3">
              {hover.point.year ?? "year unknown"} ·{" "}
              {VERDICT_META[hover.point.bucket as Verdict]?.label ?? hover.point.bucket}
            </p>
            <p className="mt-0.5 text-xs text-ink-3">
              {hover.degree === 0
                ? "No close neighbors"
                : `${hover.degree} close ${hover.degree === 1 ? "neighbor" : "neighbors"}${hover.clustered ? " · in a cluster" : ""}`}
            </p>
          </div>
        )}
      </div>
      {placementPoint && (
        <figcaption className="mt-2.5 text-xs text-ink-3">
          <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
            <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: "var(--accent)" }} aria-hidden />
            Your question
          </span>
        </figcaption>
      )}
    </figure>
  );
}
