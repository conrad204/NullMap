/**
 * A force-directed layout for the corpus map, in the manner of Obsidian's graph view.
 *
 * Every drawn study and every region center is a node. Nodes repel one another
 * (a Barnes-Hut approximation, so a map of thousands of points costs n log n per
 * tick rather than n²), a collision force keeps any two from overlapping at the
 * base zoom regardless of how densely the corpus packs a corner of the plane, a
 * weak spring ties each study to the region it currently belongs to, and a spring
 * to each node's own projected coordinates keeps the picture semantically the
 * picture the projection found. The projection seeds the layout; the forces
 * only relax the crowding, never rearrange the meaning.
 *
 * Positions live in the plane's own units. Sizes that mean something on screen
 * (a mark's radius, the reach of the repulsion) are given in pixels and converted
 * each tick with the fitted scale, so the layout is stable under pan and zoom.
 *
 * Dependency-free on purpose: the module is imported by a node:test file.
 */

export type NodeKind = "point" | "region";

export interface LayoutInput {
  id: string;
  /** Projected coordinates: the position this node is anchored to. */
  x: number;
  y: number;
  /** For a study, the id of the region it belongs to; for a region, its own id. */
  region: number;
  kind: NodeKind;
}

export interface LayoutOptions {
  /** Alpha is raised at least this far when nodes arrive, so the graph visibly settles them in. */
  reheat: number;
  /** Alpha is raised at least this far when only anchors moved, as they do on every clustering pass. */
  nudge: number;
  alphaDecay: number;
  alphaMin: number;
  velocityDecay: number;
  /** Repulsion between two studies, in px²; regions repel `regionWeight` times as hard. */
  repulsion: number;
  regionWeight: number;
  /** Repulsion is not felt beyond this many pixels; keeps the field local, like Obsidian's. */
  repulsionReach: number;
  /** Drawn radius of a study and of a region mark at the base zoom, in pixels. */
  pointRadius: number;
  regionRadius: number;
  /** Extra clearance kept between marks, in pixels. */
  collisionPadding: number;
  collisionStrength: number;
  /** Pull toward the node's own projected coordinates. */
  anchor: number;
  /** Fraction of the way a region node moves toward its reported center each tick; the clustering moves it. */
  regionAnchor: number;
  /** Pull of a study toward its region's node. */
  link: number;
  /** Pull of the whole graph's centroid toward the centroid of the projection. */
  center: number;
  /** Barnes-Hut accuracy: a cell is treated as one body when width / distance < theta. */
  theta: number;
}

export const DEFAULT_OPTIONS: LayoutOptions = {
  reheat: 0.45,
  nudge: 0.2,
  alphaDecay: 0.028,
  alphaMin: 0.003,
  velocityDecay: 0.4,
  repulsion: 6,
  regionWeight: 12,
  repulsionReach: 90,
  pointRadius: 1.3,
  regionRadius: 4.5,
  collisionPadding: 1.4,
  collisionStrength: 0.8,
  anchor: 0.035,
  regionAnchor: 0.2,
  link: 0.012,
  center: 0.02,
  theta: 0.9,
};

const KIND_POINT = 0;
const KIND_REGION = 1;
const MAX_DEPTH = 24;

/** A small deterministic jiggle, so coincident nodes separate the same way every run. */
function jiggler(seed: number): () => number {
  let state = seed >>> 0 || 1;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return ((state / 4294967296) - 0.5) * 1e-6;
  };
}

function grow<T extends Float64Array | Int32Array | Uint8Array>(array: T, size: number): T {
  const next = new (array.constructor as new (n: number) => T)(size);
  next.set(array);
  return next;
}

export class ForceLayout {
  readonly options: LayoutOptions;
  alpha = 0;
  /** Milliseconds the last tick took, smoothed; the layout coarsens itself when this climbs. */
  tickMs = 0;

  private count = 0;
  private capacity = 256;
  private x = new Float64Array(this.capacity);
  private y = new Float64Array(this.capacity);
  private vx = new Float64Array(this.capacity);
  private vy = new Float64Array(this.capacity);
  private homeX = new Float64Array(this.capacity);
  private homeY = new Float64Array(this.capacity);
  private fixedX = new Float64Array(this.capacity).fill(NaN);
  private fixedY = new Float64Array(this.capacity).fill(NaN);
  private region = new Int32Array(this.capacity);
  private kind = new Uint8Array(this.capacity);
  private radius = new Float64Array(this.capacity);
  private strength = new Float64Array(this.capacity);
  private chain = new Int32Array(this.capacity);
  private ids: string[] = [];
  private index = new Map<string, number>();
  private regionIndex = new Map<number, number>();

  // Quadtree, rebuilt every tick. Cells are squares; a leaf holds a chain of
  // coincident nodes, an internal cell always holds four children.
  private quadCount = 0;
  private quadCapacity = 1024;
  private qChild = new Int32Array(this.quadCapacity * 4);
  private qFirst = new Int32Array(this.quadCapacity);
  private qX0 = new Float64Array(this.quadCapacity);
  private qY0 = new Float64Array(this.quadCapacity);
  private qSize = new Float64Array(this.quadCapacity);
  private qX = new Float64Array(this.quadCapacity);
  private qY = new Float64Array(this.quadCapacity);
  private qMass = new Float64Array(this.quadCapacity);
  private qRadius = new Float64Array(this.quadCapacity);
  private stack = new Int32Array(this.quadCapacity);
  private readonly jiggle = jiggler(0x9e3779b9);
  private ticks = 0;

  constructor(options: Partial<LayoutOptions> = {}) {
    this.options = { ...DEFAULT_OPTIONS, ...options };
  }

  get size(): number {
    return this.count;
  }

  get settled(): boolean {
    return this.alpha < this.options.alphaMin;
  }

  idAt(i: number): string {
    return this.ids[i];
  }

  indexOf(id: string): number {
    return this.index.get(id) ?? -1;
  }

  xAt(i: number): number {
    return this.x[i];
  }

  yAt(i: number): number {
    return this.y[i];
  }

  kindAt(i: number): NodeKind {
    return this.kind[i] === KIND_REGION ? "region" : "point";
  }

  regionAt(i: number): number {
    return this.region[i];
  }

  /** Index of the node standing for a region, or -1 while the clustering has not reported it. */
  regionNode(region: number): number {
    return this.regionIndex.get(region) ?? -1;
  }

  /**
   * Brings the layout up to date with what the server has sent.
   *
   * Nodes it has never seen enter at their projected coordinates, which is the
   * only place there is any reason to put them. Nodes it has seen keep the
   * position the forces gave them and only have their anchor and region updated.
   * Nothing is removed here; see `retain`.
   */
  update(inputs: readonly LayoutInput[]): void {
    let arrived = 0;
    let moved = false;
    for (const input of inputs) {
      const existing = this.index.get(input.id);
      if (existing !== undefined) {
        if (this.homeX[existing] !== input.x || this.homeY[existing] !== input.y) moved = true;
        if (this.region[existing] !== input.region) moved = true;
        this.homeX[existing] = input.x;
        this.homeY[existing] = input.y;
        this.region[existing] = input.region;
        continue;
      }
      const i = this.append(input);
      if (input.kind === "region") this.regionIndex.set(input.region, i);
      arrived += 1;
    }
    if (arrived) this.alpha = Math.max(this.alpha, this.options.reheat);
    else if (moved) this.alpha = Math.max(this.alpha, this.options.nudge);
  }

  /** Drops every node whose id is not in `ids`, keeping the survivors where they are. */
  retain(ids: ReadonlySet<string>): void {
    let write = 0;
    for (let read = 0; read < this.count; read += 1) {
      if (!ids.has(this.ids[read])) continue;
      if (write !== read) {
        this.x[write] = this.x[read];
        this.y[write] = this.y[read];
        this.vx[write] = this.vx[read];
        this.vy[write] = this.vy[read];
        this.homeX[write] = this.homeX[read];
        this.homeY[write] = this.homeY[read];
        this.fixedX[write] = this.fixedX[read];
        this.fixedY[write] = this.fixedY[read];
        this.region[write] = this.region[read];
        this.kind[write] = this.kind[read];
        this.ids[write] = this.ids[read];
      }
      write += 1;
    }
    if (write === this.count) return;
    this.ids.length = write;
    this.count = write;
    this.index.clear();
    this.regionIndex.clear();
    for (let i = 0; i < write; i += 1) {
      this.index.set(this.ids[i], i);
      if (this.kind[i] === KIND_REGION) this.regionIndex.set(this.region[i], i);
    }
    this.alpha = Math.max(this.alpha, this.options.nudge);
  }

  /** Holds a node under the pointer; it still pushes its neighbors around. */
  pin(i: number, x: number, y: number): void {
    this.fixedX[i] = x;
    this.fixedY[i] = y;
    this.x[i] = x;
    this.y[i] = y;
    this.vx[i] = 0;
    this.vy[i] = 0;
    this.alpha = Math.max(this.alpha, this.options.reheat);
  }

  release(i: number): void {
    this.fixedX[i] = NaN;
    this.fixedY[i] = NaN;
  }

  isPinned(i: number): boolean {
    return !Number.isNaN(this.fixedX[i]);
  }

  /** The nearest node within `radius` plane units of a point, or -1. */
  nearest(x: number, y: number, radius: number): number {
    let best = -1;
    let bestDistance = radius * radius;
    for (let i = 0; i < this.count; i += 1) {
      const dx = this.x[i] - x;
      const dy = this.y[i] - y;
      const distance = dx * dx + dy * dy;
      if (distance < bestDistance) {
        bestDistance = distance;
        best = i;
      }
    }
    return best;
  }

  /**
   * Advances the layout one step. `pixelsPerUnit` is the fitted scale at the
   * base zoom, which turns the pixel-sized options into plane units.
   */
  tick(pixelsPerUnit: number): void {
    if (!this.count || this.settled) return;
    const started = typeof performance !== "undefined" ? performance.now() : 0;
    const unit = 1 / Math.max(pixelsPerUnit, 1e-9);
    const { options, alpha } = this;
    this.ticks += 1;

    // Coarser when slow: a wider theta and a collision pass every other tick
    // keep the frame rate rather than the last decimal of the layout.
    const slow = this.tickMs > 9;
    const theta = slow ? Math.min(1.6, options.theta * 1.5) : options.theta;

    for (let i = 0; i < this.count; i += 1) {
      const regionNode = this.kind[i] === KIND_REGION;
      this.radius[i] = ((regionNode ? options.regionRadius : options.pointRadius) + options.collisionPadding) * unit;
      this.strength[i] = -options.repulsion * (regionNode ? options.regionWeight : 1) * unit * unit;
    }

    this.buildQuadtree();
    this.manyBody(alpha, theta, unit);
    if (!slow || this.ticks % 2 === 0) this.collide();
    this.springs(alpha);

    const decay = 1 - options.velocityDecay;
    for (let i = 0; i < this.count; i += 1) {
      if (!Number.isNaN(this.fixedX[i])) {
        this.x[i] = this.fixedX[i];
        this.y[i] = this.fixedY[i];
        this.vx[i] = 0;
        this.vy[i] = 0;
        continue;
      }
      this.vx[i] *= decay;
      this.vy[i] *= decay;
      this.x[i] += this.vx[i];
      this.y[i] += this.vy[i];
    }

    this.alpha += (0 - this.alpha) * options.alphaDecay;
    if (started) {
      const took = performance.now() - started;
      this.tickMs = this.tickMs ? this.tickMs * 0.8 + took * 0.2 : took;
    }
  }

  /** Runs the layout to rest, or for at most `budgetMs`, without animating it. */
  settle(pixelsPerUnit: number, budgetMs = 40): void {
    const started = typeof performance !== "undefined" ? performance.now() : 0;
    let steps = 0;
    while (!this.settled && steps < 600) {
      this.tick(pixelsPerUnit);
      steps += 1;
      if (started && performance.now() - started > budgetMs) break;
    }
  }

  private append(input: LayoutInput): number {
    if (this.count === this.capacity) {
      this.capacity *= 2;
      this.x = grow(this.x, this.capacity);
      this.y = grow(this.y, this.capacity);
      this.vx = grow(this.vx, this.capacity);
      this.vy = grow(this.vy, this.capacity);
      this.homeX = grow(this.homeX, this.capacity);
      this.homeY = grow(this.homeY, this.capacity);
      this.fixedX = grow(this.fixedX, this.capacity).fill(NaN, this.count);
      this.fixedY = grow(this.fixedY, this.capacity).fill(NaN, this.count);
      this.region = grow(this.region, this.capacity);
      this.kind = grow(this.kind, this.capacity);
      this.radius = grow(this.radius, this.capacity);
      this.strength = grow(this.strength, this.capacity);
      this.chain = grow(this.chain, this.capacity);
    }
    const i = this.count;
    this.count += 1;
    this.x[i] = input.x + this.jiggle();
    this.y[i] = input.y + this.jiggle();
    this.vx[i] = 0;
    this.vy[i] = 0;
    this.homeX[i] = input.x;
    this.homeY[i] = input.y;
    this.fixedX[i] = NaN;
    this.fixedY[i] = NaN;
    this.region[i] = input.region;
    this.kind[i] = input.kind === "region" ? KIND_REGION : KIND_POINT;
    this.ids[i] = input.id;
    this.index.set(input.id, i);
    return i;
  }

  private newQuad(x0: number, y0: number, size: number): number {
    if (this.quadCount === this.quadCapacity) {
      this.quadCapacity *= 2;
      this.qChild = grow(this.qChild, this.quadCapacity * 4);
      this.qFirst = grow(this.qFirst, this.quadCapacity);
      this.qX0 = grow(this.qX0, this.quadCapacity);
      this.qY0 = grow(this.qY0, this.quadCapacity);
      this.qSize = grow(this.qSize, this.quadCapacity);
      this.qX = grow(this.qX, this.quadCapacity);
      this.qY = grow(this.qY, this.quadCapacity);
      this.qMass = grow(this.qMass, this.quadCapacity);
      this.qRadius = grow(this.qRadius, this.quadCapacity);
      this.stack = new Int32Array(this.quadCapacity);
    }
    const q = this.quadCount;
    this.quadCount += 1;
    this.qChild[q * 4] = -1;
    this.qFirst[q] = -1;
    this.qX0[q] = x0;
    this.qY0[q] = y0;
    this.qSize[q] = size;
    return q;
  }

  private split(q: number): void {
    const half = this.qSize[q] / 2;
    const x0 = this.qX0[q];
    const y0 = this.qY0[q];
    const a = this.newQuad(x0, y0, half);
    const b = this.newQuad(x0 + half, y0, half);
    const c = this.newQuad(x0, y0 + half, half);
    const d = this.newQuad(x0 + half, y0 + half, half);
    this.qChild[q * 4] = a;
    this.qChild[q * 4 + 1] = b;
    this.qChild[q * 4 + 2] = c;
    this.qChild[q * 4 + 3] = d;
  }

  private childFor(q: number, x: number, y: number): number {
    const half = this.qSize[q] / 2;
    const right = x >= this.qX0[q] + half ? 1 : 0;
    const below = y >= this.qY0[q] + half ? 2 : 0;
    return this.qChild[q * 4 + right + below];
  }

  private buildQuadtree(): void {
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (let i = 0; i < this.count; i += 1) {
      const x = this.x[i];
      const y = this.y[i];
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    }
    const size = Math.max(maxX - minX, maxY - minY, 1e-9) * (1 + 1e-6);
    this.quadCount = 0;
    this.newQuad(minX, minY, size);

    for (let i = 0; i < this.count; i += 1) {
      const x = this.x[i];
      const y = this.y[i];
      let q = 0;
      let depth = 0;
      for (;;) {
        if (this.qChild[q * 4] !== -1) {
          q = this.childFor(q, x, y);
          depth += 1;
          continue;
        }
        const first = this.qFirst[q];
        if (first === -1) {
          this.qFirst[q] = i;
          this.chain[i] = -1;
          break;
        }
        if (depth >= MAX_DEPTH || (this.x[first] === x && this.y[first] === y)) {
          this.chain[i] = first;
          this.qFirst[q] = i;
          break;
        }
        // A leaf already holding a different position: push its chain down a level.
        this.split(q);
        const target = this.childFor(q, this.x[first], this.y[first]);
        this.qFirst[target] = first;
        this.qFirst[q] = -1;
        q = this.childFor(q, x, y);
        depth += 1;
      }
    }

    // Children are created after their parent, so a reverse pass sees every
    // child's totals before its parent's.
    for (let q = this.quadCount - 1; q >= 0; q -= 1) {
      let mass = 0;
      let cx = 0;
      let cy = 0;
      let radius = 0;
      if (this.qChild[q * 4] === -1) {
        for (let i = this.qFirst[q]; i !== -1; i = this.chain[i]) {
          const weight = Math.abs(this.strength[i]);
          mass += this.strength[i];
          cx += weight * this.x[i];
          cy += weight * this.y[i];
          if (this.radius[i] > radius) radius = this.radius[i];
        }
      } else {
        for (let k = 0; k < 4; k += 1) {
          const child = this.qChild[q * 4 + k];
          const weight = Math.abs(this.qMass[child]);
          mass += this.qMass[child];
          cx += weight * this.qX[child];
          cy += weight * this.qY[child];
          if (this.qRadius[child] > radius) radius = this.qRadius[child];
        }
      }
      const weight = Math.abs(mass);
      this.qMass[q] = mass;
      this.qX[q] = weight ? cx / weight : this.qX0[q] + this.qSize[q] / 2;
      this.qY[q] = weight ? cy / weight : this.qY0[q] + this.qSize[q] / 2;
      this.qRadius[q] = radius;
    }
  }

  private manyBody(alpha: number, theta: number, unit: number): void {
    const theta2 = theta * theta;
    const distanceMin2 = unit * unit;
    const reach = this.options.repulsionReach * unit;
    const distanceMax2 = reach * reach;
    const { stack, x, y, vx, vy, strength, chain, qMass, qX, qY, qX0, qY0, qSize, qChild, qFirst } = this;
    for (let i = 0; i < this.count; i += 1) {
      const xi = x[i];
      const yi = y[i];
      let top = 0;
      stack[top] = 0;
      top += 1;
      while (top > 0) {
        top -= 1;
        const q = stack[top];
        const mass = qMass[q];
        if (!mass) continue;
        // Nothing in a cell wholly beyond the reach can be felt, so it and its
        // children are skipped without looking at where their mass sits.
        const x0 = qX0[q];
        const y0 = qY0[q];
        const width = qSize[q];
        if (x0 > xi + reach || x0 + width < xi - reach || y0 > yi + reach || y0 + width < yi - reach) continue;
        let dx = qX[q] - xi;
        let dy = qY[q] - yi;
        let l = dx * dx + dy * dy;
        if ((width * width) / theta2 < l) {
          if (l < distanceMax2) {
            if (dx === 0) { dx = this.jiggle(); l += dx * dx; }
            if (dy === 0) { dy = this.jiggle(); l += dy * dy; }
            if (l < distanceMin2) l = Math.sqrt(distanceMin2 * l);
            const w = (mass * alpha) / l;
            vx[i] += dx * w;
            vy[i] += dy * w;
          }
          continue;
        }
        if (qChild[q * 4] !== -1) {
          stack[top] = qChild[q * 4];
          stack[top + 1] = qChild[q * 4 + 1];
          stack[top + 2] = qChild[q * 4 + 2];
          stack[top + 3] = qChild[q * 4 + 3];
          top += 4;
          continue;
        }
        for (let j = qFirst[q]; j !== -1; j = chain[j]) {
          if (j === i) continue;
          let ddx = x[j] - xi;
          let ddy = y[j] - yi;
          let ll = ddx * ddx + ddy * ddy;
          if (ll >= distanceMax2) continue;
          if (ddx === 0) { ddx = this.jiggle(); ll += ddx * ddx; }
          if (ddy === 0) { ddy = this.jiggle(); ll += ddy * ddy; }
          if (ll < distanceMin2) ll = Math.sqrt(distanceMin2 * ll);
          const w = (strength[j] * alpha) / ll;
          vx[i] += ddx * w;
          vy[i] += ddy * w;
        }
      }
    }
  }

  private collide(): void {
    const strength = this.options.collisionStrength;
    const { stack, x, y, vx, vy, radius, chain, qRadius, qX0, qY0, qSize, qChild, qFirst } = this;
    for (let i = 0; i < this.count; i += 1) {
      const ri = radius[i];
      const ri2 = ri * ri;
      const xi = x[i] + vx[i];
      const yi = y[i] + vy[i];
      let top = 0;
      stack[top] = 0;
      top += 1;
      while (top > 0) {
        top -= 1;
        const q = stack[top];
        const reach = ri + qRadius[q];
        const x0 = qX0[q];
        const y0 = qY0[q];
        const size = qSize[q];
        if (x0 > xi + reach || x0 + size < xi - reach || y0 > yi + reach || y0 + size < yi - reach) continue;
        if (qChild[q * 4] !== -1) {
          stack[top] = qChild[q * 4];
          stack[top + 1] = qChild[q * 4 + 1];
          stack[top + 2] = qChild[q * 4 + 2];
          stack[top + 3] = qChild[q * 4 + 3];
          top += 4;
          continue;
        }
        for (let j = qFirst[q]; j !== -1; j = chain[j]) {
          if (j <= i) continue;
          const rj = radius[j];
          const r = ri + rj;
          let dx = xi - (x[j] + vx[j]);
          let dy = yi - (y[j] + vy[j]);
          let l = dx * dx + dy * dy;
          if (l >= r * r) continue;
          if (dx === 0) { dx = this.jiggle(); l += dx * dx; }
          if (dy === 0) { dy = this.jiggle(); l += dy * dy; }
          l = Math.sqrt(l);
          const push = ((r - l) / l) * strength;
          dx *= push;
          dy *= push;
          const rj2 = rj * rj;
          const share = rj2 / (ri2 + rj2);
          vx[i] += dx * share;
          vy[i] += dy * share;
          vx[j] -= dx * (1 - share);
          vy[j] -= dy * (1 - share);
        }
      }
    }
  }

  private springs(alpha: number): void {
    const { anchor, regionAnchor, link, center } = this.options;
    let sumX = 0;
    let sumY = 0;
    let homeSumX = 0;
    let homeSumY = 0;
    for (let i = 0; i < this.count; i += 1) {
      const regionNode = this.kind[i] === KIND_REGION;
      if (regionNode) {
        // A region mark is the clustering's own report of where the region is, so
        // it eases to that report instead of negotiating with the field.
        this.x[i] += (this.homeX[i] - this.x[i]) * regionAnchor;
        this.y[i] += (this.homeY[i] - this.y[i]) * regionAnchor;
      } else {
        this.vx[i] += (this.homeX[i] - this.x[i]) * anchor * alpha;
        this.vy[i] += (this.homeY[i] - this.y[i]) * anchor * alpha;
        const home = this.regionIndex.get(this.region[i]);
        if (home !== undefined) {
          this.vx[i] += (this.x[home] - this.x[i]) * link * alpha;
          this.vy[i] += (this.y[home] - this.y[i]) * link * alpha;
        }
      }
      sumX += this.x[i];
      sumY += this.y[i];
      homeSumX += this.homeX[i];
      homeSumY += this.homeY[i];
    }
    const shiftX = ((homeSumX - sumX) / this.count) * center;
    const shiftY = ((homeSumY - sumY) / this.count) * center;
    if (!shiftX && !shiftY) return;
    for (let i = 0; i < this.count; i += 1) {
      if (!Number.isNaN(this.fixedX[i])) continue;
      this.x[i] += shiftX;
      this.y[i] += shiftY;
    }
  }
}
