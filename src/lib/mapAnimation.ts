import { useEffect, useRef, useState } from "react";
import type { MapPoint } from "../types";

/** A region centroid as the stream reports it: a position, with no label yet. */
export interface Centroid {
  id: number;
  size: number;
  x: number;
  y: number;
}

export interface MapFrame {
  points: MapPoint[];
  centroids: Centroid[];
}

/** Fraction of the remaining distance closed each frame; ~0.18 settles in ~0.3 s at 60 Hz. */
const EASE = 0.18;
/** Below this, in plane units scaled by the frame's extent, the motion is over. */
const EPSILON = 1e-4;

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined"
    && typeof window.matchMedia === "function"
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

interface Tween {
  x: number;
  y: number;
}

/**
 * Eases a streamed map towards each new state instead of cutting to it.
 *
 * Only positions are interpolated, and only between states the server actually
 * sent: a point rests at its own projected coordinates and a centroid at its
 * own, so nothing on screen at rest is invented. A point that has just arrived
 * has no previous position, so it enters from the centroid of the region it was
 * assigned to and settles outward — an entrance, not a claim about where it was.
 * With `prefers-reduced-motion` set, every state is applied immediately.
 */
export function useSettlingMap(target: MapFrame, animate: boolean): MapFrame {
  const [frame, setFrame] = useState<MapFrame>(target);
  const positions = useRef(new Map<string, Tween>());
  const centroids = useRef(new Map<number, Tween>());
  const request = useRef<number | null>(null);

  useEffect(() => {
    const still = !animate || prefersReducedMotion();
    const centroidOf = (id: number) => target.centroids.find((centroid) => centroid.id === id);

    // Seed anything new: points from their region's mark, centroids where they are.
    for (const point of target.points) {
      if (positions.current.has(point.id)) continue;
      const home = still ? undefined : centroidOf(point.region);
      positions.current.set(point.id, { x: home?.x ?? point.x, y: home?.y ?? point.y });
    }
    for (const centroid of target.centroids) {
      if (!centroids.current.has(centroid.id)) centroids.current.set(centroid.id, { ...centroid });
    }

    const step = () => {
      let moving = false;
      const ease = still ? 1 : EASE;
      const points = target.points.map((point) => {
        const tween = positions.current.get(point.id)!;
        tween.x += (point.x - tween.x) * ease;
        tween.y += (point.y - tween.y) * ease;
        moving = moving || Math.abs(point.x - tween.x) > EPSILON || Math.abs(point.y - tween.y) > EPSILON;
        return { ...point, x: tween.x, y: tween.y };
      });
      const marks = target.centroids.map((centroid) => {
        const tween = centroids.current.get(centroid.id)!;
        tween.x += (centroid.x - tween.x) * ease;
        tween.y += (centroid.y - tween.y) * ease;
        moving = moving
          || Math.abs(centroid.x - tween.x) > EPSILON
          || Math.abs(centroid.y - tween.y) > EPSILON;
        return { ...centroid, x: tween.x, y: tween.y };
      });
      setFrame({ points, centroids: marks });
      request.current = moving ? requestAnimationFrame(step) : null;
    };

    if (request.current !== null) cancelAnimationFrame(request.current);
    if (still || typeof requestAnimationFrame !== "function") {
      step();
      if (request.current !== null) cancelAnimationFrame(request.current);
      request.current = null;
    } else {
      request.current = requestAnimationFrame(step);
    }
    return () => {
      if (request.current !== null) cancelAnimationFrame(request.current);
      request.current = null;
    };
  }, [target, animate]);

  return frame;
}
