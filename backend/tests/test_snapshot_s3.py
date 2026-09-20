import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import duckdb
import httpx
import pytest

from app.config import Settings
from app.ingest.__main__ import parser, run
from app.ingest.fetch import fetch_pages
from app.ingest.materialize import materialize
from app.ingest.openalex import normalize_work
from app.ingest.snapshot import load_manifest, plan_manifest, public_url, scan_snapshot


def work(identifier, title, abstract=None, **kwargs):
    return {
        "id": f"https://openalex.org/{identifier}",
        "title": title,
        "type": "article",
        "publication_year": 1990,
        "abstract_inverted_index": json.dumps(abstract) if abstract else None,
        "topics": [],
        "referenced_works": [],
        **kwargs,
    }


def parquet(tmp_path, rows, name="part"):
    source = tmp_path / f"{name}.jsonl"
    source.write_text("".join(json.dumps(row) + "\n" for row in rows))
    target = tmp_path / f"{name}.parquet"
    with duckdb.connect() as db:
        db.read_json(str(source)).write_parquet(str(target))
    return target


def read(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openalex.org/works",
        "s3://private-bucket/data.parquet",
        "https://openalex.s3.amazonaws.com/data/parquet/works/part.parquet?token=secret",
        "https://openalex.s3.amazonaws.com/legacy-data/works/part.gz",
    ],
)
def test_snapshot_accepts_only_unsigned_public_works_urls(url):
    with pytest.raises(ValueError, match="unsigned public"):
        public_url(url)


def test_public_manifest_needs_no_credentials_and_plan_reports_partial_coverage():
    observed = []
    data = {
        "entity": "works",
        "format": "parquet",
        "date": "2026-06-25",
        "files": [
            {
                "url": f"s3://openalex/data/parquet/works/updated_date=2026-06-24/{i}.parquet",
                "meta": {"content_length": 100},
            }
            for i in range(3)
        ],
    }

    def handler(request):
        observed.append(request)
        return httpx.Response(200, json=data)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            manifest = await load_manifest(
                "s3://openalex/data/parquet/works/manifest.json", client=client
            )
        plan = plan_manifest(manifest, max_files=1)
        assert plan["selected_parts"] == 1 and plan["total_parts"] == 3
        assert plan["all_parts_selected"] is False
        assert plan["physical_bytes_budgeted"] == 100
        assert plan_manifest(manifest)["all_parts_selected"] is True

    asyncio.run(exercise())
    assert (
        str(observed[0].url) == "https://openalex.s3.amazonaws.com/data/parquet/works/manifest.json"
    )
    assert "authorization" not in observed[0].headers and not observed[0].url.query


def test_scope_retains_old_metadata_and_taxonomy_matches_and_exact_abstract_phrases(tmp_path):
    rows = [
        work("W1", "Renal outcomes", publication_year=1960),
        work("W2", "A treatment study", {"high": [0], "blood": [1], "pressure": [2]}),
        work("W3", "An unrelated foot procedure", {"Foot": [0], "pain": [1]}),
        work(
            "W4",
            "Replacement therapy",
            topics=[
                {
                    "id": "https://openalex.org/T999",
                    "subfield": {"id": "https://openalex.org/subfields/2727"},
                }
            ],
        ),
        work("W5", "Hypertension handbook", type="book"),
        work("W6", "Adrenal signaling"),
    ]
    path = parquet(tmp_path, rows)
    output = tmp_path / "out.jsonl"
    result = scan_snapshot(
        [str(path)], output, profile="hypertension-kidney", all_parts_selected=True
    )
    assert {x["id"].rsplit("/", 1)[-1] for x in read(output)} == {"W1", "W2", "W4", "W5"}
    assert result["complete_snapshot_scope"] is True
    metadata = normalize_work(read(output)[0])
    assert metadata["year"] == 1960 and metadata["abstract_available"] is False
    assert normalize_work(next(x for x in read(output) if x["type"] == "book"))["is_review"] is True


def test_midpart_resume_rolls_back_uncommitted_output_and_row_cap_is_not_complete(tmp_path):
    path = parquet(tmp_path, [work(f"W{i}", "Kidney trial") for i in range(1, 5)])
    output = tmp_path / "out.jsonl"
    first = scan_snapshot([str(path)], output, limit=1, all_parts_selected=True, batch_size=3)
    assert first["records"] == 1 and first["complete_snapshot_scope"] is False
    with output.open("a") as handle:
        handle.write("incomplete write after crash")
    final = scan_snapshot([str(path)], output, resume=True, all_parts_selected=True, batch_size=3)
    assert final["records"] == 4 and final["complete_snapshot_scope"] is True
    assert len({x["id"] for x in read(output)}) == 4
    with pytest.raises(ValueError, match="changed"):
        scan_snapshot([str(path)], output, resume=True, profile="hypertension-kidney")


def test_interrupted_commit_resumes_without_duplicate_or_lost_records(tmp_path):
    path = parquet(tmp_path, [work(f"W{i}", "Kidney trial") for i in range(1, 4)])
    output = tmp_path / "out.jsonl"

    def fail_after_commit(state):
        raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="simulated"):
        scan_snapshot([str(path)], output, batch_size=1, progress=fail_after_commit)
    final = scan_snapshot([str(path)], output, batch_size=1, resume=True)
    assert final["records"] == 3 and len(read(output)) == 3


def test_reference_backfill_uses_s3_ids_even_outside_topic_profile(tmp_path):
    path = parquet(
        tmp_path, [work("W1", "Kidney trial"), work("W2", "General statistical methods")]
    )
    output = tmp_path / "references.jsonl"
    result = scan_snapshot([str(path)], output, profile="hypertension-kidney", work_ids={"W2"})
    assert result["records"] == 1
    assert read(output)[0]["id"].endswith("W2")
    assert read(output)[0]["snapshot_provenance"]["scope"] == "reference_backfill"


def test_source_change_and_disk_shortage_preserve_existing_checkpoint(tmp_path, monkeypatch):
    path = parquet(tmp_path, [work("W1", "Kidney trial"), work("W2", "Renal outcomes")])
    output = tmp_path / "out.jsonl"
    scan_snapshot([str(path)], output, limit=1)
    monkeypatch.setattr("app.ingest.snapshot.shutil.disk_usage", lambda _: SimpleNamespace(free=0))
    with pytest.raises(RuntimeError, match="free space"):
        scan_snapshot([str(path)], output, resume=True)
    assert len(read(output)) == 1
    with path.open("ab") as handle:
        handle.write(b"changed")
    with pytest.raises(ValueError, match="changed"):
        scan_snapshot([str(path)], output, resume=True)


def test_openalex_api_fetch_rejected_before_any_network_call(tmp_path):
    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: pytest.fail("Network call"))
        ) as client:
            with pytest.raises(ValueError, match="public S3"):
                await fetch_pages("openalex", tmp_path / "raw.jsonl", client=client)

    asyncio.run(exercise())


def test_reference_ids_excludes_existing_works_and_nonreview_citations(tmp_path):
    source = tmp_path / "works.jsonl"
    source.write_text(
        "\n".join(
            json.dumps(x)
            for x in [
                work(
                    "W1",
                    "Kidney systematic review",
                    referenced_works=["https://openalex.org/W2", "https://openalex.org/W3"],
                ),
                work("W2", "Kidney trial", referenced_works=["https://openalex.org/W4"]),
            ]
        )
    )
    output = tmp_path / "missing.txt"
    args = parser().parse_args(["reference-ids", "--input", str(source), "--output", str(output)])
    assert asyncio.run(run(args))["missing_reference_ids"] == 1
    assert output.read_text() == "W3\n"


def test_materialization_reuses_checkpoint_and_indexes_metadata_without_llm(tmp_path):
    class Repository:
        index = "test"

        def __init__(self):
            self.rows = {}
            self.calls = 0

        async def bulk_upsert(self, rows):
            self.calls += 1
            self.rows.update({x["id"]: x for x in rows})

    class Embedder:
        def encode(self, texts):
            return [[1.0, 0.0] for text in texts]

    async def exercise():
        source = tmp_path / "works.jsonl"
        source.write_text(
            "\n".join(json.dumps(work(f"W{i}", "Kidney trial")) for i in [1, 2]) + "\n"
        )
        repo = Repository()
        config = Settings(_env_file=None)
        first = await materialize(source, repo, embedder=Embedder(), batch_size=1, config=config)
        assert first["records"] == 2 and first["metadata_only"] == 2
        assert first["embedded"] == 2 and first["complete"] is True
        assert all(
            x["estimate"] is None and x["result_label"] == "no_result_stated"
            for x in repo.rows.values()
        )
        assert await materialize(source, repo, embedder=Embedder(), config=config) == first
        assert repo.calls == 2

    asyncio.run(exercise())


def test_runtime_source_modules_have_no_openalex_api_endpoint():
    root = Path(__file__).parents[1] / "app"
    forbidden = "api." + "openalex.org"
    assert not [str(p.relative_to(root)) for p in root.rglob("*.py") if forbidden in p.read_text()]


def test_snapshot_timestamp_columns_serialize_without_timezone_dependency(tmp_path):
    path = tmp_path / "timestamps.parquet"
    with duckdb.connect() as db:
        db.sql(
            "SELECT 'https://openalex.org/W1' AS id, 'Kidney trial' AS title, "
            "TIMESTAMPTZ '2026-06-26 00:00:00+00' AS updated_date"
        ).write_parquet(str(path))
    output = tmp_path / "out.jsonl"
    scan_snapshot([str(path)], output)
    assert read(output)[0]["updated_date"].startswith("2026-06-26")


def test_materialization_handles_blank_lines_and_appended_scan_output(tmp_path):
    class Repository:
        index = "test"

        def __init__(self):
            self.ids = []

        async def bulk_upsert(self, rows):
            self.ids.extend(row["id"] for row in rows)

    async def exercise():
        source = tmp_path / "works.jsonl"
        source.write_text("\n\n" + json.dumps(work("W1", "Kidney trial")) + "\n\n")
        source.with_suffix(".jsonl.checkpoint.json").write_text(
            json.dumps({"signature": "stable-source"})
        )
        repo = Repository()
        first = await materialize(source, repo, batch_size=1, config=Settings(_env_file=None))
        assert first["records"] == 1
        with source.open("a") as handle:
            handle.write(json.dumps(work("W2", "Renal outcomes")) + "\n")
        final = await materialize(source, repo, batch_size=1, config=Settings(_env_file=None))
        assert final["records"] == 2 and final["complete"]
        assert repo.ids == ["W1", "W2"]

    asyncio.run(exercise())


def test_bootstrap_scans_links_and_indexes_then_resumes_without_reindexing(tmp_path, monkeypatch):
    from test_ingest import trial

    from app.bootstrap import bootstrap
    from app.bootstrap import parser as bootstrap_parser
    from app.ingest.fetch import atomic_json

    class Repository:
        index = "test-s3"

        def __init__(self):
            self.client = self
            self.rows = {}
            self.batches = 0
            self.closed = False

        async def ensure_index(self):
            pass

        async def get_many(self, ids, **kwargs):
            return [self.rows[x] for x in ids if x in self.rows]

        async def bulk_upsert(self, rows):
            self.batches += 1
            self.rows.update({x["id"]: {**x, "record_kind": "study"} for x in rows})

        async def persist_links(self, merged):
            for row in merged:
                self.rows[row["id"]] = row
                for paper in row["linked_papers"]:
                    self.rows[paper["id"]]["record_kind"] = "linked_publication"

        async def health(self):
            return {
                "connected": True,
                "studies": sum(
                    x.get("record_kind") != "linked_publication" for x in self.rows.values()
                ),
            }

        async def close(self):
            self.closed = True

    async def scan(client, **kwargs):
        for doc in list(client.rows.values()):
            if doc.get("record_kind") == "study" and not doc.get("is_review"):
                yield {"_source": doc}

    async def registry(source, output, **kwargs):
        assert source == "ctgov" and kwargs["filters"] == "" and kwargs["limit"] is None
        assert "hypertension" in kwargs["query"] and "kidney" in kwargs["query"]
        raw = trial()
        raw["protocolSection"]["referencesModule"]["references"] = [
            {"pmid": "1234", "type": "RESULT"}
        ]
        output.write_text(json.dumps(raw) + "\n")
        atomic_json(output.with_suffix(".jsonl.checkpoint.json"), {"signature": "registry-fixture"})
        return {"done": True, "count": 1}

    class Embedder:
        def encode(self, texts):
            return [[1.0, 0.0] for _ in texts]

    repo = Repository()
    monkeypatch.setattr("app.repository.ElasticRepository", lambda: repo)
    monkeypatch.setattr("app.embeddings.get_embedder", lambda *args: Embedder())
    monkeypatch.setattr("app.ingest.materialize.async_scan", scan)
    monkeypatch.setattr("app.bootstrap.fetch_pages", registry)
    first = parquet(
        tmp_path,
        [
            work("W1", "Kidney trial", {"Renal": [0], "outcomes": [1]}, ids={"pmid": "1234"}),
            work("W3", "Unrelated foot study"),
        ],
        name="first",
    )
    second = parquet(tmp_path, [work("W2", "Hypertension study")], name="second")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "date": "2026-06-26",
                "files": [
                    {"url": str(path), "size_bytes": path.stat().st_size}
                    for path in [first, second]
                ],
            }
        )
    )
    args = bootstrap_parser().parse_args(
        [
            "--run",
            "--manifest",
            str(manifest),
            "--data-dir",
            str(tmp_path / "build"),
            "--batch-size",
            "1",
        ]
    )
    result = asyncio.run(bootstrap(args))
    assert result["complete"] and result["snapshot"]["records"] == 2 and repo.closed
    assert set(repo.rows) == {"W1", "W2", "NCT00000001"}
    canonical = repo.rows["NCT00000001"]
    assert canonical["source"] == "merged" and canonical["estimate"] == 0.2
    assert canonical["snapshot_provenance"]["snapshot_date"] == "2026-06-26"
    assert repo.rows["W1"]["record_kind"] == "linked_publication"
    assert repo.rows["W2"]["abstract_available"] is False
    assert result["index"]["studies"] == 2
    before = repo.batches
    assert asyncio.run(bootstrap(args))["complete"]
    assert repo.batches == before


def test_cli_refuses_changed_pinned_manifest(tmp_path):
    path = parquet(tmp_path, [work("W1", "Kidney trial")])
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {"date": "2026-06-26", "files": [{"url": str(path), "size_bytes": path.stat().st_size}]}
        )
    )
    args = parser().parse_args(
        [
            "snapshot",
            "--manifest",
            str(manifest),
            "--output",
            str(tmp_path / "out.jsonl"),
            "--resume",
        ]
    )
    assert asyncio.run(run(args))["complete_snapshot_scope"]
    changed = json.loads(manifest.read_text())
    changed["date"] = "2026-09-26"
    manifest.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="manifest changed"):
        asyncio.run(run(args))


def _manifest(sizes):
    return {"date": "2026-06-26", "files": [
        {"url": f"https://openalex.s3.amazonaws.com/data/parquet/works/p{i}.parquet",
         "size_bytes": size} for i, size in enumerate(sizes)]}


def test_largest_first_covers_more_bytes_without_changing_the_part_set():
    from app.ingest.snapshot import plan_manifest
    manifest = _manifest([10, 5000, 30, 4000, 20])
    default = plan_manifest(manifest, max_files=2)
    biggest = plan_manifest(manifest, max_files=2, largest_first=True)
    assert default["physical_bytes_budgeted"] == 5010
    assert biggest["physical_bytes_budgeted"] == 9000
    assert default["selection_order"] == "manifest"
    assert biggest["selection_order"] == "largest_first"
    assert [f["size_bytes"] for f in biggest["files"]] == [5000, 4000]
    # Neither claims complete coverage, and the fraction is reported honestly.
    assert default["all_parts_selected"] is False and biggest["all_parts_selected"] is False
    assert biggest["selected_bytes_fraction"] == round(9000 / 9060, 6)
    # Ordering alone must not invent or drop parts on a full run.
    full_default = plan_manifest(manifest)
    full_sorted = plan_manifest(manifest, largest_first=True)
    assert full_sorted["all_parts_selected"] is True
    assert full_sorted["selected_bytes_fraction"] == 1.0
    assert sorted(f["url"] for f in full_default["files"]) == sorted(
        f["url"] for f in full_sorted["files"]
    )
    assert full_default["manifest_sha256"] == full_sorted["manifest_sha256"]


def test_skip_files_continues_a_partial_run_without_claiming_the_release():
    from app.ingest.snapshot import plan_manifest
    manifest = _manifest([10, 5000, 30, 4000, 20])
    first = plan_manifest(manifest, max_files=2)
    rest = plan_manifest(manifest, skip_files=2)
    chunk = plan_manifest(manifest, skip_files=2, max_files=2)
    assert [f["size_bytes"] for f in rest["files"]] == [30, 4000, 20]
    assert [f["size_bytes"] for f in chunk["files"]] == [30, 4000]
    assert rest["skipped_parts"] == 2 and first["skipped_parts"] == 0
    # Same release identity, and the tail alone is never the complete scope.
    assert rest["manifest_sha256"] == first["manifest_sha256"]
    assert rest["all_parts_selected"] is False
    assert rest["selected_bytes_fraction"] == round(4050 / 9060, 6)
    with pytest.raises(ValueError):
        plan_manifest(manifest, skip_files=5)
    with pytest.raises(ValueError):
        plan_manifest(manifest, skip_files=-1)


def test_reordering_parts_invalidates_a_scan_checkpoint(tmp_path):
    import json

    import pytest

    from app.ingest.snapshot import scan_snapshot
    a = tmp_path / "a.parquet"
    b = tmp_path / "b.parquet"
    for path in (a, b):
        path.write_bytes(b"not really parquet")
    out = tmp_path / "works.jsonl"
    checkpoint = out.with_suffix(out.suffix + ".checkpoint.json")
    out.write_text("")
    checkpoint.write_text(json.dumps({
        "signature": "stale", "snapshot_id": "x", "records": 0, "bytes": 0,
        "part": 0, "selected_parts": 2, "selected_parts_complete": False}))
    with pytest.raises(ValueError, match="changed"):
        scan_snapshot([str(b), str(a)], out, max_bytes=10_000, resume=True, snapshot_id="x",
                      remote_sizes=None, profile="hypertension-kidney")
