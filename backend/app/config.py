from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env", extra="ignore"
    )

    elastic_url: str = "http://localhost:9200"
    elastic_api_key: str = ""
    elastic_index: str = "studies"
    elastic_local: bool = False
    openai_api_key: str = ""
    openalex_snapshot_manifest: str = (
        "https://openalex.s3.amazonaws.com/data/parquet/works/manifest.json"
    )
    hf_token: str = ""
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dimensions: int = 384
    embedding_device: str = "cpu"
    embeddings_enabled: bool = True
    reference_min_similarity: float = Field(default=0.55, ge=0, le=1)
    classifier_model_path: str = ""
    small_model: str = "gpt-4.1-mini"
    narration_model: str = "gpt-4.1"
    extraction_limit: int = Field(default=100, ge=0, le=200)
    llm_concurrency: int = Field(default=5, ge=1, le=20)
    # USD per million tokens: configurable assumptions, never claimed as a bill.
    small_input_price: float = 0.4
    small_cached_price: float = 0.1
    small_output_price: float = 1.6
    narration_input_price: float = 2.0
    narration_cached_price: float = 0.5
    narration_output_price: float = 8.0
    ttc_api_key: str = ""
    ttc_model: str = "bear-2"
    compression_enabled: bool = False
    compression_validated: bool = False
    ttc_input_price: float = 0.0
    request_timeout: float = 45.0
    extraction_cache_version: str = "v4-result-direction"
    # Query-time full text from Europe PMC for papers with a PMCID; abstract otherwise.
    fulltext_enabled: bool = True
    europepmc_url: str = "https://www.ebi.ac.uk/europepmc/webservices/rest"
    fulltext_timeout: float = 20.0
    fulltext_max_lines: int = Field(default=160, ge=20, le=600)
    # Gap map: every embedded study, scanned in pages and clustered once.
    # The region count is a resolution, and what counts as an empty band depends on
    # it: measured on 6000 documents of the live index, 24 regions leave the corpus
    # continuous (no band under the occupancy threshold) while 80 keep a median of
    # 64 primary attempts per region and still expose bands.
    gapmap_regions: int = Field(default=80, ge=2, le=200)
    gapmap_seed: int = 0
    # A cap on how much of the embedded corpus one build reads; 0 is no cap, and
    # the coverage line and the warning always report what was actually read.
    # Measured on the live index (2,078,515 embedded studies, 384 dimensions):
    # the scan runs at ~11k documents/s and the vectors weigh ~3.0 GiB, so an
    # uncapped build is ~3 minutes and needs a host with several GB to spare.
    gapmap_scan_limit: int = Field(default=0, ge=0)
    # Page size and concurrent point-in-time slices for that scan. Sixteen slices
    # measured ~11k docs/s against ~3.2k for a single unsliced walk.
    gapmap_batch: int = Field(default=2000, ge=100, le=10000)
    gapmap_slices: int = Field(default=16, ge=1, le=64)
    # How many of the clustered studies the canvas draws. Every study shapes the
    # regions; a browser cannot paint two million marks, so the drawn subset is
    # thinned by a hash of the document id.
    gapmap_points: int = Field(default=6000, ge=100, le=50000)
    # Rows the 2-D projection is fitted on. The basis is a drawing choice, and a
    # full-corpus SVD would stall the build for minutes to move points by pixels.
    gapmap_projection_sample: int = Field(default=50000, ge=1000, le=500000)
    # Bound on the full-corpus k-means. Measured at 200k documents and 80
    # regions: 10 iterations ~3.2 s, 40 ~7.7 s; the assignment usually settles
    # first and the loop stops when it does.
    gapmap_iterations: int = Field(default=25, ge=1, le=200)
    # Minimum seconds between two streamed frames while the corpus is scanning.
    gapmap_tick_seconds: float = Field(default=0.4, ge=0.0, le=10.0)
    max_concurrent_searches: int = 4
    frontend_dist: str = str(Path(__file__).resolve().parents[2] / "dist")
    cors_origins: list[str] = ["http://localhost:5173"]


settings = Settings()
