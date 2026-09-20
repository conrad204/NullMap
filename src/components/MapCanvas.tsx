import { useCallback, useEffect, useRef, useState, type MouseEvent, type PointerEvent } from "react";
import { ArrowsOut, Minus, Plus } from "@phosphor-icons/react";
import type { MapGap, MapPoint, MapRegion, RegionLabel, Verdict } from "../types";
import { REGION_META, REGION_ORDER } from "../lib/regions";
import { VERDICT_META } from "../lib/verdicts";

interface MapCanvasProps {
  points: MapPoint[];
  regions: MapRegion[];
  gaps: MapGap[];
  placementPoint?: { x: number; y: number } | null;
}

/** Region label -> the theme variable the canvas resolves at draw time. */
const LABEL_VAR: Record<RegionLabel, string> = {
  active: "--v-effect",
  contested: "--v-inconclusive",
  null_saturated: "--v-reported-null",
  dark: "--v-unreported",
  unread: "--ink-3",
  thin: "--ink-3",
};

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

/** The fitted map-to-canvas transform, kept for hover hit-testing. */
interface View {
  width: number;
  height: number;
  toX: (x: number) => number;
  toY: (y: number) => number;
}

interface Hover {
  left: number;
  top: number;
  below: boolean;
  point: MapPoint;
}

export default function MapCanvas({ points, regions, gaps, placementPoint = null }: MapCanvasProps) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const viewRef = useRef<View | null>(null);
  const [hover, setHover] = useState<Hover | null>(null);
  const [transform, setTransform] = useState<Transform>(IDENTITY);
  const dragRef = useRef<{ pointerId: number; x: number; y: number; moved: boolean } | null>(null);

  // A new map is a new picture, so it starts framed rather than wherever the last one was left.
  useEffect(() => setTransform(IDENTITY), [points, regions, gaps]);

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
      if (placementPoint) include(placementPoint.x, placementPoint.y);
      if (!isFinite(minX)) {
        viewRef.current = null;
        return;
      }
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
        regionColor.set(region.id, color(LABEL_VAR[region.label], grey));
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

      for (const point of points) {
        ctx.fillStyle = regionColor.get(point.region) ?? grey;
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
  }, [points, regions, gaps, placementPoint, transform]);

  /** Zoom about a point in canvas pixels, so whatever is under the cursor stays under it. */
  const zoomAt = useCallback((factor: number, px: number, py: number) => {
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
    if (!best) {
      setHover(null);
      return;
    }
    const x = view.toX(best.x);
    const y = view.toY(best.y);
    setHover({
      left: Math.min(Math.max(x, 96), rect.width - 96),
      top: y,
      below: y < 70,
      point: best,
    });
  }

  const present = REGION_ORDER.filter((label) =>
    regions.some((region) => region.label === label),
  );
  const ariaLabel =
    `Scatter map of ${points.length} sampled studies in ${regions.length} regions` +
    (gaps.length ? ", with dashed lines marking open bands between literatures" : "") +
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
          style={{ cursor: dragRef.current ? "grabbing" : hover ? "pointer" : "grab" }}
          onMouseMove={handleMove}
          onMouseLeave={() => setHover(null)}
          onPointerDown={handlePointerDown}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerUp}
          onDoubleClick={() => setTransform(IDENTITY)}
        />
        <div className="absolute right-2 top-2 flex flex-col gap-1">
          {[
            { label: "Zoom in", icon: <Plus size={14} weight="bold" />, run: () => zoomCentre(1.4), off: transform.k >= MAX_ZOOM },
            { label: "Zoom out", icon: <Minus size={14} weight="bold" />, run: () => zoomCentre(1 / 1.4), off: transform.k <= MIN_ZOOM },
            { label: "Reset the view", icon: <ArrowsOut size={14} weight="bold" />, run: () => setTransform(IDENTITY), off: transform.k === 1 && transform.tx === 0 && transform.ty === 0 },
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
          </div>
        )}
      </div>
      <figcaption className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-ink-3">
        <span>Drag to pan, scroll to zoom{transform.k > 1 ? ` · ${transform.k.toFixed(1)}×` : ""}</span>
        {present.map((label) => (
          <span key={label} className="inline-flex items-center gap-1.5">
            <span
              className="h-2 w-2 rounded-full"
              style={{ backgroundColor: `var(${LABEL_VAR[label]})` }}
              aria-hidden
            />
            {REGION_META[label].label}
          </span>
        ))}
        {gaps.length > 0 && (
          <span className="inline-flex items-center gap-1.5">
            <span
              className="w-3.5"
              style={{ borderTop: "2px dashed var(--line-strong)" }}
              aria-hidden
            />
            open band
          </span>
        )}
        {placementPoint && (
          <span className="inline-flex items-center gap-1.5">
            <span
              className="h-2.5 w-2.5 rounded-full"
              style={{ border: "2px solid var(--accent)" }}
              aria-hidden
            />
            your idea
          </span>
        )}
      </figcaption>
    </figure>
  );
}
