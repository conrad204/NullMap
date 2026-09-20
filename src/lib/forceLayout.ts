/**
 * A force-directed layout for the corpus map, in the manner of Obsidian's graph view.
 *
 * Every drawn study is a node and nothing else is. Nodes all repel one another
 * alike (a Barnes-Hut approximation, so a map of thousands of points costs
 * n log n per tick rather than n²), a collision force keeps any two from
 * overlapping at the base zoom regardless of how densely the corpus packs a
 * corner of the plane, and a spring joins each pair of studies whose embeddings
 * agree closely, pulling harder the closer they agree. Clusters are whatever
 * those springs and the repulsion settle into; none is drawn in advance. A weak
 * spring to each node's own projected coordinates keeps the picture roughly
 * where the projection put it, so the layout starts meaningful and stays so.
 *
 * Positions live in the plane's own units. Sizes that mean something on screen
 * (a mark's radius, the reach of the repulsion) are given in pixels and converted
 * each tick with the fitted scale, so the layout is stable under pan and zoom.
 *
 * Dependency-free on purpose: the module is imported by a node:test file.
 */

export interface LayoutInput {
  id: string;
  /** Projected coordinates: the position this node is anchored to. */
  x: number;
  y: number;
}

/** A spring between two nodes, by id, with the cosine similarity that justifies it. */
export type LayoutLink = readonly [source: string, target: string, cosine: number];

export interface LayoutOptions {
  /** Alpha is raised at least this far when nodes arrive, so the graph visibly settles them in. */
  reheat: number;
  /** Alpha is raised at least this far when only anchors or links changed. */
  nudge: number;
  alphaDecay: number;
  alphaMin: number;
  velocityDecay: number;
  /** Repulsion between two studies, in px²; the same for every node. */
  repulsion: number;
  /** Repulsion is not felt beyond this many pixels; keeps the field local, like Obsidian's. */
  repulsionReach: number;
  /** Drawn radius at the base zoom, in pixels, of a study with no links and of the best-connected one. */
  pointRadius: number;
  maxRadius: number;
  /** Links at and beyond which a node is drawn at `maxRadius`. */
  fullDegree: number;
  /** Extra clearance kept between marks, in pixels. */
  collisionPadding: number;
  collisionStrength: number;
  /** Pull toward the node's own projected coordinates. */
  anchor: number;
  /** Spring strength of a link whose cosine is 1; weaker links scale down from it. */
  link: number;
  /** Cosine at which a link's pull reaches zero: the edge floor the server draws at. */
  linkFloor: number;
  /** Rest length of a link, in pixels, for a cosine at the floor; tighter as the cosine climbs. */
  linkDistance: number;
  /** Pull of the whole graph's centroid toward the centroid of the projection. */
  center: number;
  /** Barnes-Hut accuracy: a cell is treated as one body when width / distance < theta. */
  theta: number;
}

/**
 * A node's drawn radius in pixels: its size says how many papers it is linked
 * to, rising with the square root of the count so a hub is visibly a hub
 * without swallowing its neighbors.
 */
export function nodeRadius(degree: number, options: LayoutOptions = DEFAULT_OPTIONS): number {
  const share = Math.min(1, Math.sqrt(Math.max(0, degree) / options.fullDegree));
  return options.pointRadius + (options.maxRadius - options.pointRadius) * share;
}

export const DEFAULT_OPTIONS: LayoutOptions = {
  reheat: 0.45,
  nudge: 0.2,
  alphaDecay: 0.028,
  alphaMin: 0.003,
  velocityDecay: 0.4,
  repulsion: 36,
  repulsionReach: 260,
  pointRadius: 2.2,
  maxRadius: 7.5,
  fullDegree: 8,
  collisionPadding: 2.4,
  collisionStrength: 0.85,
  anchor: 0.008,
  link: 0.7,
  linkFloor: 0.78,
  linkDistance: 44,
  center: 0.006,
  theta: 0.9,
};

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
  private radius = new Float64Array(this.capacity);
  private strength = new Float64Array(this.capacity);
  private chain = new Int32Array(this.capacity);
  private ids: string[] = [];
  private index = new Map<string, number>();

  // Links, by id so they survive nodes being dropped, and resolved to indexes
  // only when the set of nodes has changed since the last tick.
  private links: LayoutLink[] = [];
  private linkKeys = new Set<string>();
  private linkI = new Int32Array(0);
  private linkJ = new Int32Array(0);
  private linkW = new Float64Array(0);
  private degree = new Int32Array(this.capacity);
  private linksStale = true;

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

  get linkCount(): number {
    return this.links.length;
  }

  /** How many links this node has, to nodes the layout holds. */
  degreeAt(i: number): number {
    this.resolveLinks();
    return this.degree[i];
  }

  /** The node's drawn radius at the base zoom, in pixels. */
  radiusAt(i: number): number {
    return nodeRadius(this.degreeAt(i), this.options);
  }

  /** Every link, as `[i, j, cosine]` over current node indexes; pairs whose ends are gone are skipped. */
  eachLink(visit: (i: number, j: number, cosine: number) => void): void {
    this.resolveLinks();
    for (let l = 0; l < this.linkI.length; l += 1) visit(this.linkI[l], this.linkJ[l], this.linkW[l]);
  }

  /**
   * Brings the layout up to date with what the server has sent.
   *
   * Nodes it has never seen enter at their projected coordinates, which is the
   * only place there is any reason to put them. Nodes it has seen keep the
   * position the forces gave them and only have their anchor updated.
   * Nothing is removed here; see `retain`.
   */
  update(inputs: readonly LayoutInput[]): void {
    let arrived = 0;
    let moved = false;
    for (const input of inputs) {
      const existing = this.index.get(input.id);
      if (existing !== undefined) {
        if (this.homeX[existing] !== input.x || this.homeY[existing] !== input.y) moved = true;
        this.homeX[existing] = input.x;
        this.homeY[existing] = input.y;
        continue;
      }
      this.append(input);
      arrived += 1;
    }
    if (arrived) {
      this.linksStale = true;
      this.alpha = Math.max(this.alpha, this.options.reheat);
    } else if (moved) this.alpha = Math.max(this.alpha, this.options.nudge);
  }

  /**
   * Adds springs between pairs of studies. A pair already linked keeps its
   * first cosine; a link naming a node the layout has not seen waits for it.
   */
  link(links: readonly LayoutLink[]): void {
    let added = 0;
    for (const link of links) {
      const key = link[0] < link[1] ? `${link[0]}\u0000${link[1]}` : `${link[1]}\u0000${link[0]}`;
      if (this.linkKeys.has(key)) continue;
      this.linkKeys.add(key);
      this.links.push(link);
      added += 1;
    }
    if (!added) return;
    this.linksStale = true;
    this.alpha = Math.max(this.alpha, this.options.nudge);
  }

  /** Forgets every link; the nodes stay. */
  unlink(): void {
    if (!this.links.length) return;
    this.links = [];
    this.linkKeys.clear();
    this.linksStale = true;
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
        this.ids[write] = this.ids[read];
      }
      write += 1;
    }
    if (write === this.count) return;
    this.ids.length = write;
    this.count = write;
    this.index.clear();
    for (let i = 0; i < write; i += 1) this.index.set(this.ids[i], i);
    this.linksStale = true;
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

    this.resolveLinks();
    const strength = -options.repulsion * unit * unit;
    for (let i = 0; i < this.count; i += 1) {
      this.radius[i] = (nodeRadius(this.degree[i], options) + options.collisionPadding) * unit;
      this.strength[i] = strength;
    }

    this.buildQuadtree();
    this.manyBody(alpha, theta, unit);
    if (!slow || this.ticks % 2 === 0) this.collide();
    this.attract(alpha, unit);
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
      this.radius = grow(this.radius, this.capacity);
      this.degree = grow(this.degree, this.capacity);
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

  private resolveLinks(): void {
    if (!this.linksStale) return;
    this.linksStale = false;
    const i: number[] = [];
    const j: number[] = [];
    const w: number[] = [];
    this.degree.fill(0);
    for (const [source, target, cosine] of this.links) {
      const a = this.index.get(source);
      const b = this.index.get(target);
      if (a === undefined || b === undefined || a === b) continue;
      i.push(a);
      j.push(b);
      w.push(cosine);
      this.degree[a] += 1;
      this.degree[b] += 1;
    }
    this.linkI = Int32Array.from(i);
    this.linkJ = Int32Array.from(j);
    this.linkW = Float64Array.from(w);
  }

  /**
   * The link springs. A link's pull grows linearly with how far its cosine
   * stands above the floor, and its rest length shrinks the same way, so two
   * near-duplicates sit almost on top of each other while a pair that barely
   * cleared the floor is held loosely at arm's length.
   */
  private attract(alpha: number, unit: number): void {
    this.resolveLinks();
    const { link, linkFloor, linkDistance } = this.options;
    const { x, y, vx, vy, linkI, linkJ, linkW } = this;
    const span = Math.max(1 - linkFloor, 1e-6);
    for (let l = 0; l < linkI.length; l += 1) {
      const i = linkI[l];
      const j = linkJ[l];
      const weight = Math.min(1, Math.max(0, (linkW[l] - linkFloor) / span));
      if (!weight) continue;
      const rest = linkDistance * (1 - 0.7 * weight) * unit;
      let dx = x[j] + vx[j] - x[i] - vx[i];
      let dy = y[j] + vy[j] - y[i] - vy[i];
      if (dx === 0) dx = this.jiggle();
      if (dy === 0) dy = this.jiggle();
      const length = Math.sqrt(dx * dx + dy * dy);
      const pull = ((length - rest) / length) * alpha * link * weight;
      dx *= pull;
      dy *= pull;
      vx[j] -= dx * 0.5;
      vy[j] -= dy * 0.5;
      vx[i] += dx * 0.5;
      vy[i] += dy * 0.5;
    }
  }

  private springs(alpha: number): void {
    const { anchor, center } = this.options;
    let sumX = 0;
    let sumY = 0;
    let homeSumX = 0;
    let homeSumY = 0;
    for (let i = 0; i < this.count; i += 1) {
      this.vx[i] += (this.homeX[i] - this.x[i]) * anchor * alpha;
      this.vy[i] += (this.homeY[i] - this.y[i]) * anchor * alpha;
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
