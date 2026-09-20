"""Serves the gap map: cluster the studies around a question, describe them, place the question.

A map with a question is built from that question's neighborhood: the
``gapmap_neighborhood`` studies nearest its vector, fetched by kNN, clustered
and drawn. That is a few hundred documents, so the build takes about a second
and the picture is one a reader can take in. Its coverage says exactly that —
the N nearest studies out of the embedded index — and never more.

Without a question there is nothing to center on, so the map falls back to the
whole embedded index. Covering the whole index means the build is long enough
to be worth watching, so it is written as a stream: documents are scanned in
pages, provisional centroids move as pages arrive, and full-corpus k-means then
runs to convergence, emitting the real intermediate state after every iteration.
Nothing emitted is interpolated or replayed — a partial map is labelled partial,
and only the final event describes the whole corpus. Measured on the live index
(2,096,736 embedded studies): 264 s end to end — roughly 130 s to scan at sixteen
slices and 25 bounded k-means iterations over the whole corpus in the rest,
peaking at 6.9 GB.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import zlib
from collections.abc import Awaitable, Callable

import numpy as np

from app.config import Settings, settings
from app.embeddings import QueryEmbedder
from app.gapmap import (
    MAP_VERSION,
    RunningClusters,
    _bucket,
    assign,
    calibrate,
    combine,
    describe_regions,
    find_gaps,
    fit_projection,
    kmeans_steps,
    neighbor_edges,
    normalize,
    place,
    project_with,
)
from app.models import MapArithmetic

logger = logging.getLogger(__name__)

Progress = Callable[[dict], Awaitable[None]]

# Question maps kept in memory beside the corpus map.
MAP_CACHE = 16


def _public(region: dict) -> dict:
    """Region without its centroid: a 384-float vector is not UI payload."""
    return {key: value for key, value in region.items() if key != "centroid"}


def _drawn(identifier: str, stride: int) -> bool:
    """Whether a document is one of the ones drawn on the canvas.

    A browser cannot render two million marks, so the canvas shows a subset —
    chosen by a hash of the document id, which is deterministic, independent of
    the order pages happen to arrive in, and unrelated to anything the map
    labels. Every document is still clustered; only the drawing is thinned.
    """
    return stride <= 1 or zlib.crc32(identifier.encode("utf-8")) % stride == 0


class MapBuild:
    """The corpus as it accumulates: vectors in one matrix, documents beside it.

    The matrix is grown by doubling and handed out as a view, so clustering a
    corpus of millions never holds two copies of it and adding a page costs the
    page, not the corpus. The embedding is dropped from each document once it
    has been copied into the matrix: keeping both is three gigabytes of Python
    floats for nothing.
    """

    def __init__(self, corpus: int, capacity: int = 0):
        self.corpus = corpus
        self.documents: list[dict] = []
        self.dimensions: int | None = None
        self._capacity = max(1024, capacity)
        self._buffer: np.ndarray | None = None
        self._rows = 0

    def _reserve(self, rows: int) -> None:
        assert self.dimensions is not None
        if self._buffer is None:
            self._buffer = np.empty((self._capacity, self.dimensions), dtype=np.float32)
        while self._rows + rows > len(self._buffer):
            grown = np.empty((len(self._buffer) * 2, self.dimensions), dtype=np.float32)
            grown[: self._rows] = self._buffer[: self._rows]
            self._buffer = grown

    def plan(self, rows: int) -> None:
        """Grow to the size the scan expects, in one step rather than by doubling."""
        self._capacity = max(self._capacity, rows)
        if self.dimensions is None or rows <= 0:
            return
        if self._buffer is None or len(self._buffer) < rows:
            grown = np.empty((rows, self.dimensions), dtype=np.float32)
            if self._buffer is not None:
                grown[: self._rows] = self._buffer[: self._rows]
            self._buffer = grown

    def add(self, documents: list[dict]) -> int:
        """Append a page's vectors to the matrix; returns how many were usable."""
        rows, kept = [], []
        for document in documents:
            embedding = document.pop("embedding", None)
            if not isinstance(embedding, (list, tuple)) or not embedding:
                continue
            vector = np.asarray(embedding, dtype=np.float32)
            if not np.isfinite(vector).all() or not vector.any():
                continue
            if self.dimensions is None:
                self.dimensions = len(vector)
            if len(vector) != self.dimensions:
                continue
            rows.append(vector)
            kept.append(document)
        if not rows:
            return 0
        self._reserve(len(rows))
        assert self._buffer is not None
        self._buffer[self._rows : self._rows + len(rows)] = normalize(np.vstack(rows))
        self._rows += len(rows)
        self.documents.extend(kept)
        return len(kept)

    @property
    def vectors(self) -> np.ndarray:
        if self._buffer is None:
            return np.zeros((0, self.dimensions or 1), dtype=np.float32)
        return self._buffer[: self._rows]

    def __len__(self) -> int:
        return self._rows


class GapMapService:
    def __init__(self, repository, config: Settings = settings):
        self.repo = repository
        self.config = config
        self.embedder = QueryEmbedder(config)
        # One cached map per question (keyed by its vector) plus the corpus map.
        self._maps: dict[bytes | None, dict] = {}
        self._lock = asyncio.Lock()
        self._subscribers: set[asyncio.Queue] = set()
        self._tasks: dict[bytes | None, asyncio.Task] = {}
        # The plane the current build is drawing on, so a streamed question can be
        # placed on the same one before the build finishes.
        self._basis: tuple[np.ndarray, np.ndarray] | None = None

    async def embed(self, text: str) -> list[float] | None:
        return await self.embedder.embed(text)

    # ---------------------------------------------------------------- building

    async def _publish(self, payload: dict) -> None:
        for queue in list(self._subscribers):
            if queue.full():  # a slow reader must not stall the build
                continue
            queue.put_nowait(payload)

    @property
    def _built(self) -> dict | None:
        """The cached corpus map, if it has been built."""
        return self._maps.get(None)

    async def build(self, refresh: bool = False, vector: np.ndarray | None = None) -> dict:
        """The cached map for this question (or the corpus), building it once if needed.

        The build is a task of its own rather than a coroutine of whichever
        request arrived first: a viewer who closes the tab after twenty seconds
        must not throw away a clustering run that other viewers, and the cache,
        are waiting for.
        """
        key = None if vector is None else np.asarray(vector, dtype=np.float32).tobytes()
        if key in self._maps and not refresh:
            return self._maps[key]
        async with self._lock:
            if key in self._maps and not refresh:
                return self._maps[key]
            task = self._tasks.get(key)
            if task is None or task.done():
                task = asyncio.create_task(self._build(vector))
                self._tasks[key] = task
        built = await asyncio.shield(task)
        if len(self._maps) >= MAP_CACHE and key not in self._maps:
            for stale in [k for k in self._maps if k is not None][: len(self._maps) - MAP_CACHE + 1]:
                del self._maps[stale]
        self._maps[key] = built
        return built

    async def _build(self, vector: np.ndarray | None) -> dict:
        if vector is None:
            return await self._build_corpus()
        return await self._build_neighborhood(vector)

    async def _build_neighborhood(self, vector: np.ndarray) -> dict:
        """Fetch the studies nearest the question, cluster them, describe the result."""
        counting = asyncio.create_task(self.repo.count_embedded())
        size = self.config.gapmap_neighborhood
        query = normalize(np.asarray(vector, dtype=np.float32)[None, :])[0]
        documents = await self.repo.knn_studies(query.tolist(), size)
        build = MapBuild(0, capacity=size)
        build.add(documents)
        stride = max(1, len(build) // max(1, self.config.gapmap_points))
        drawn = [
            row
            for row in range(len(build))
            if _drawn(str(build.documents[row].get("id", row)), stride)
        ]
        basis: tuple[np.ndarray, np.ndarray] | None = None
        if len(build):
            basis = await asyncio.to_thread(fit_projection, build.vectors)
            self._basis = basis
        corpus = max(0, await counting)
        build.corpus = max(corpus, len(build))
        scope = {"scope": "neighborhood", "neighborhood": size}
        centroids = None
        labels = np.zeros(len(build), dtype=np.int64)
        iteration = 0
        emitted = 0
        if len(build):
            steps = kmeans_steps(
                build.vectors,
                self.config.gapmap_regions,
                seed=self.config.gapmap_seed,
                iterations=self.config.gapmap_iterations,
            )
            while True:
                step = await asyncio.to_thread(next, steps, None)
                if step is None:
                    break
                centroids, labels = step
                iteration += 1
                if basis is not None:
                    emitted = await self._emit_partial(
                        build,
                        centroids,
                        basis,
                        drawn,
                        emitted,
                        len(build),
                        iteration=iteration,
                        labels=labels,
                        extra={"coverage": scope},
                    )
        return await self._finish(build, centroids, labels, basis, drawn, iteration, scope)

    async def _build_corpus(self) -> dict:
        """Scan the embedded corpus, cluster it, and describe the result."""
        # Counted beside the scan rather than before it: the count is a query of
        # its own over two million documents, and nothing can be drawn while it
        # is the only thing running.
        counting = asyncio.create_task(self.repo.count_embedded())
        limit = max(0, self.config.gapmap_scan_limit)
        # Until the corpus size is known the scan cannot thin the drawn subset to
        # fit the canvas, so the first pages are drawn whole up to half the point
        # budget; the stride is set properly as soon as the count lands.
        stride = max(1, limit // max(1, self.config.gapmap_points)) if limit else 1
        early = self.config.gapmap_points // 2
        build = MapBuild(0, capacity=limit)
        target = limit
        clusters = RunningClusters(self.config.gapmap_regions, self.config.gapmap_seed)
        drawn: list[int] = []  # row indices of the documents the canvas shows
        basis: tuple[np.ndarray, np.ndarray] | None = None
        emitted = 0
        ticked = 0.0
        loop = asyncio.get_running_loop()

        async for batch in self.repo.scan_embedded(
            batch_size=self.config.gapmap_batch,
            slices=self.config.gapmap_slices,
            limit=limit,
        ):
            start = len(build)
            build.add(batch)
            for row in range(start, len(build)):
                if not build.corpus and len(drawn) >= early:
                    break
                if _drawn(str(build.documents[row].get("id", row)), stride):
                    drawn.append(row)
            if not build.corpus and counting.done():
                corpus = max(0, counting.result())
                build.corpus = corpus
                target = min(corpus, limit) if limit else corpus
                stride = max(1, target // max(1, self.config.gapmap_points))
                build.plan(target)
            await asyncio.to_thread(clusters.update, build.vectors[start:])
            if basis is None and len(build) >= self.config.gapmap_projection_sample:
                basis = await asyncio.to_thread(
                    fit_projection, build.vectors[: self.config.gapmap_projection_sample]
                )
                self._basis = basis
            # One frame per interval, not one per page: at eleven thousand
            # documents a second the pages are far faster than anything a canvas
            # can show, and every frame resends where each drawn point now sits.
            if (
                basis is not None
                and clusters.centroids is not None
                and loop.time() - ticked >= self.config.gapmap_tick_seconds
            ):
                ticked = loop.time()
                emitted = await self._emit_partial(
                    build, clusters.centroids, basis, drawn, emitted, target
                )

        corpus = max(0, await counting)
        # A live index grows while it is read, so a scan can finish holding more
        # documents than the count taken at its start. The number actually read
        # is the one that was measured; the count is only the older of the two.
        corpus = max(corpus, len(build))
        build.corpus = corpus
        if basis is None and len(build):
            basis = await asyncio.to_thread(
                fit_projection, build.vectors[: self.config.gapmap_projection_sample]
            )
            self._basis = basis

        centroids = clusters.centroids
        labels = np.zeros(len(build), dtype=np.int64)
        iteration = 0
        if len(build):
            # Stepped from a thread one iteration at a time: each step is a pass
            # over the whole corpus, so running the generator inline would block
            # the event loop for exactly as long as the frames it is producing.
            steps = kmeans_steps(
                build.vectors,
                self.config.gapmap_regions,
                seed=self.config.gapmap_seed,
                iterations=self.config.gapmap_iterations,
                centroids=centroids,
            )
            while True:
                step = await asyncio.to_thread(next, steps, None)
                if step is None:
                    break
                centroids, labels = step
                iteration += 1
                if basis is not None:
                    emitted = await self._emit_partial(
                        build,
                        centroids,
                        basis,
                        drawn,
                        emitted,
                        target,
                        iteration=iteration,
                        labels=labels,
                    )

        return await self._finish(
            build, centroids, labels, basis, drawn, iteration, {"scope": "corpus"}
        )

    async def _finish(
        self,
        build: MapBuild,
        centroids: np.ndarray | None,
        labels: np.ndarray,
        basis: tuple[np.ndarray, np.ndarray] | None,
        drawn: list[int],
        iteration: int,
        scope: dict,
    ) -> dict:
        """Describe a clustered build: regions, gaps, drawn points, edges, coverage."""
        corpus = build.corpus
        clusters_used = len(centroids) if centroids is not None else 0
        regions = await asyncio.to_thread(
            describe_regions, build.documents, build.vectors, labels, clusters_used
        )
        gaps = await asyncio.to_thread(
            find_gaps, build.documents, regions, vectors=build.vectors
        )
        points, kept = self._points(build, regions, labels, basis, drawn)
        edges = await asyncio.to_thread(self._edges, build, kept)
        if basis is not None and regions:
            centre, plane = basis
            positions = project_with(
                centre,
                plane,
                np.vstack([region["centroid"] for region in regions]).astype(np.float32),
            )
            for region, (x, y) in zip(regions, positions):
                region["x"], region["y"] = float(x), float(y)
        built = {
            "documents": build.documents,
            "vectors": build.vectors,
            "regions": regions,
            "gaps": gaps,
            "points": points,
            "edges": edges,
            "basis": basis,
            "coverage": {
                "clustered": len(build),
                "corpus": corpus,
                "regions": len(regions),
                "drawn": len(points),
                "complete": True,
                **scope,
            },
            "version": MAP_VERSION,
        }
        logger.info(
            "Gap map (%s) built over %s of %s embedded studies in %s regions after %s iterations",
            scope.get("scope"),
            len(build),
            corpus,
            len(regions),
            iteration,
        )
        await self._publish({"event": "built"})
        return built

    async def _emit_partial(
        self,
        build: MapBuild,
        centroids: np.ndarray,
        basis: tuple[np.ndarray, np.ndarray],
        drawn: list[int],
        emitted: int,
        target: int,
        iteration: int | None = None,
        labels: np.ndarray | None = None,
        publish: Progress | None = None,
        extra: dict | None = None,
    ) -> int:
        """Publish the map as it stands: new points, where the regions are now.

        Only the points the canvas has not seen are sent in full; the ones it
        already holds are recoloured through ``pointRegions``, because a region
        a point belongs to changes on every k-means iteration while its position
        does not. Frames go to ``publish`` when given (one viewer's own build)
        and to every subscriber otherwise (the shared corpus build).
        """
        if publish is None and not self._subscribers:
            return emitted
        centre, plane = basis
        rows = np.array(drawn, dtype=np.int64)
        if not len(rows):
            return emitted
        if labels is None:
            member = assign(build.vectors[rows], centroids)
        else:
            member = labels[rows]
        fresh: list[dict] = []
        edges: list[list[float]] = []
        if len(rows) > emitted:
            new_rows = rows[emitted:]
            coords = project_with(centre, plane, build.vectors[new_rows])
            edges = await asyncio.to_thread(self._edges, build, rows.tolist(), emitted)
            for offset, row in enumerate(new_rows.tolist()):
                document = build.documents[row]
                title = str(document.get("title") or "")
                fresh.append(
                    {
                        "id": str(document.get("id", "")),
                        "x": float(coords[offset, 0]),
                        "y": float(coords[offset, 1]),
                        "region": int(member[emitted + offset]),
                        "bucket": _bucket(document),
                        "title": title if len(title) <= 120 else f"{title[:119]}…",
                        "year": document.get("year") if isinstance(document.get("year"), int) else None,
                    }
                )
        positions = project_with(centre, plane, centroids)
        counts = np.bincount(member, minlength=len(centroids))
        await (publish or self._publish)(
            {
                **(extra or {}),
                "event": "progress",
                "stage": "clustering" if iteration else "scanning",
                "iteration": iteration,
                "coverage": {
                    "clustered": len(build),
                    "corpus": build.corpus,
                    "regions": int((counts > 0).sum()),
                    "drawn": len(rows),
                    "target": target,
                    "complete": False,
                    **(extra or {}).get("coverage", {}),
                },
                "points": fresh,
                "edges": edges,
                "pointRegions": [int(value) for value in member.tolist()],
                "regions": [
                    {
                        "id": index,
                        "size": int(counts[index]),
                        "x": float(positions[index, 0]),
                        "y": float(positions[index, 1]),
                    }
                    for index in range(len(centroids))
                    if counts[index]
                ],
            }
        )
        return len(rows)

    def _points(
        self,
        build: MapBuild,
        regions: list[dict],
        labels: np.ndarray,
        basis: tuple[np.ndarray, np.ndarray] | None,
        drawn: list[int],
    ) -> tuple[list[dict], list[int]]:
        """2-D positions for the drawn subset, each carrying its region.

        Also returns the rows drawn, in point order, so edges can be indexed
        the same way the canvas indexes points.
        """
        if basis is None or not regions or not drawn:
            return [], []
        centre, plane = basis
        known = {region["id"] for region in regions}
        kept = [row for row in drawn if int(labels[row]) in known]
        rows = np.array(kept, dtype=np.int64)
        coords = project_with(centre, plane, build.vectors[rows])
        points = []
        for offset, row in enumerate(rows.tolist()):
            region = int(labels[row])
            document = build.documents[row]
            title = str(document.get("title") or "")
            points.append(
                {
                    "id": str(document.get("id", "")),
                    "x": float(coords[offset, 0]),
                    "y": float(coords[offset, 1]),
                    "region": region,
                    "bucket": _bucket(document),
                    "title": title if len(title) <= 120 else f"{title[:119]}…",
                    "year": document.get("year") if isinstance(document.get("year"), int) else None,
                }
            )
        return points, kept

    def _edges(self, build: MapBuild, drawn: list[int], start: int = 0) -> list[list[float]]:
        """Similarity edges among drawn points, indexed by position in ``drawn``.

        ``start`` is how many of them already have their edges, so a streamed
        frame only pays for the rows it adds. Each edge is ``[i, j, cosine]``.
        """
        if len(drawn) < 2:
            return []
        found = neighbor_edges(
            build.vectors[np.array(drawn, dtype=np.int64)],
            start=start,
            neighbors=self.config.gapmap_edge_neighbors,
            floor=self.config.gapmap_edge_floor,
        )
        return [[i, j, round(cosine, 3)] for i, j, cosine in found]

    # ---------------------------------------------------------------- serving

    def _decorate(
        self, placement: dict, vector: np.ndarray | list[float], built: dict
    ) -> dict:
        """Attach the same-plane 2-D point and strip the internal centroid."""
        placement["region"] = _public(placement["region"]) if placement["region"] else None
        if built["basis"] is not None:
            mean, basis = built["basis"]
            coords = project_with(
                mean, basis, normalize(np.asarray(vector, dtype=np.float32)[None, :])
            )
            placement["point"] = {"x": float(coords[0, 0]), "y": float(coords[0, 1])}
        return placement

    async def _expression_vector(
        self, expression: MapArithmetic, warnings: list[str]
    ) -> np.ndarray | None:
        """The unit vector of ``start − remove + add``: embed each phrase, combine."""
        terms = [expression.start, *expression.remove, *expression.add]
        vectors = await asyncio.gather(*(self.embed(term) for term in terms))
        if any(vector is None for vector in vectors):
            warnings.append("Embeddings are disabled, so the expression could not be placed.")
            return None
        n_remove = len(expression.remove)
        combined = combine(
            [vectors[0], *vectors[1 + n_remove :]], vectors[1 : 1 + n_remove]
        )
        if combined is None:
            warnings.append(
                "The expression cancels itself out; the combined vector has no direction."
            )
        return combined

    async def _place_expression(
        self, expression: MapArithmetic, combined: np.ndarray | list[float], built: dict
    ) -> dict:
        """Place the combined vector like any other point on the map.

        What comes back are the real papers nearest it, so the honesty of the
        answer does not depend on how cleanly the analogy worked.
        """
        placement = await asyncio.to_thread(
            place,
            combined,
            built["documents"],
            built["regions"],
            built["gaps"],
            vectors=built["vectors"],
        )
        placement["expression"] = {
            "start": expression.start,
            "remove": expression.remove,
            "add": expression.add,
        }
        return self._decorate(placement, combined, built)

    async def assess(
        self,
        idea: str | None = None,
        arithmetic: MapArithmetic | None = None,
        cutoff_year: int | None = None,
        refresh: bool = False,
    ) -> dict:
        warnings: list[str] = []
        vector: np.ndarray | list[float] | None = None
        if arithmetic is not None:
            vector = await self._expression_vector(arithmetic, warnings)
        elif idea:
            vector = await self.embed(idea)
            if vector is None:
                warnings.append("Embeddings are disabled, so the idea could not be placed.")
        built = await self.build(refresh=refresh, vector=vector)
        coverage = built["coverage"]
        if coverage.get("scope") == "neighborhood":
            warnings.append(
                f"The map shows the {coverage['clustered']} embedded studies nearest this "
                f"question, out of {coverage['corpus']} in the index: the question's "
                "neighborhood, not the whole index."
            )
        elif coverage["corpus"] > coverage["clustered"]:
            warnings.append(
                f"The map clusters {coverage['clustered']} of {coverage['corpus']} "
                "embedded studies, not the whole index."
            )
        if coverage["drawn"] < coverage["clustered"]:
            warnings.append(
                f"All {coverage['clustered']} clustered studies shape the regions; the canvas "
                f"draws {coverage['drawn']} of them."
            )
        placement = None
        if vector is not None and arithmetic is not None:
            placement = await self._place_expression(arithmetic, vector, built)
        elif vector is not None:
            placement = await asyncio.to_thread(
                place,
                vector,
                built["documents"],
                built["regions"],
                built["gaps"],
                vectors=built["vectors"],
            )
            placement = self._decorate(placement, vector, built)
        calibration = None
        if cutoff_year is not None:
            calibration = await asyncio.to_thread(
                calibrate,
                built["documents"],
                cutoff_year,
                self.config.gapmap_regions,
                self.config.gapmap_seed,
                vectors=built["vectors"],
            )
        return {
            "version": built["version"],
            "coverage": coverage,
            "regions": [_public(region) for region in built["regions"]],
            "gaps": built["gaps"],
            "points": built["points"],
            "edges": built.get("edges", []),
            "placement": placement,
            "calibration": calibration,
            "warnings": warnings,
        }

    async def stream(
        self,
        idea: str | None = None,
        arithmetic: MapArithmetic | None = None,
        cutoff_year: int | None = None,
        refresh: bool = False,
        progress: Progress | None = None,
    ) -> dict:
        """``assess`` with the build published as it happens.

        A cached map produces no progress events: replaying a finished build as
        though it were happening would be theatre, and the caller can tell the
        difference because the first thing it receives is the finished result.
        """
        if progress is None:
            return await self.assess(idea, arithmetic, cutoff_year, refresh)
        queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._subscribers.add(queue)
        # Embedded here rather than in the build: the build describes the corpus
        # and is shared by every viewer, while the question belongs to this one.
        asking = asyncio.create_task(self._ask(idea, arithmetic))
        pump = asyncio.create_task(self._pump(queue, progress, asking))
        try:
            return await self.assess(idea, arithmetic, cutoff_year, refresh)
        finally:
            self._subscribers.discard(queue)
            asking.cancel()
            queue.put_nowait(None)
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pump

    async def _ask(
        self, idea: str | None, arithmetic: MapArithmetic | None
    ) -> np.ndarray | None:
        """The unit vector of this viewer's question, if there is one to embed."""
        if arithmetic is not None:
            vector = await self._expression_vector(arithmetic, [])
        elif idea:
            vector = await self.embed(idea)
        else:
            return None
        if vector is None:
            return None
        return normalize(np.asarray(vector, dtype=np.float32)[None, :])[0]

    async def _pump(
        self,
        queue: asyncio.Queue,
        progress: Progress,
        asking: asyncio.Task | None = None,
    ) -> None:
        while True:
            payload = await queue.get()
            if payload is None or payload.get("event") == "built":
                return
            # The question is placed on the plane the build is drawing on, so it
            # is on the map from the first frame instead of only at the end. It
            # is where the question sits, not a claim about what surrounds it.
            if (
                asking is not None
                and asking.done()
                and not asking.cancelled()
                and asking.exception() is None
                and self._basis is not None
            ):
                vector = asking.result()
                if vector is not None:
                    centre, plane = self._basis
                    coords = project_with(centre, plane, vector[None, :])
                    payload = {
                        **payload,
                        "placement": {"x": float(coords[0, 0]), "y": float(coords[0, 1])},
                    }
            await progress({key: value for key, value in payload.items() if key != "event"})
