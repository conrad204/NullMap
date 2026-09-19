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
    openalex_api_key: str = ""
    hf_token: str = ""
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dimensions: int = 384
    embedding_device: str = "cpu"
    embeddings_enabled: bool = True
    reference_min_similarity: float = Field(default=0.55, ge=0, le=1)
    classifier_model_path: str = ""
    small_model: str = "gpt-4.1-mini"
    narration_model: str = "gpt-4.1"
    extraction_limit: int = Field(default=30, ge=0, le=40)
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
    extraction_cache_version: str = "v2-sentence-evidence"
    max_upload_bytes: int = 5 * 1024 * 1024
    max_upload_files: int = 3
    max_concurrent_searches: int = 4
    frontend_dist: str = str(Path(__file__).resolve().parents[2] / "dist")
    cors_origins: list[str] = ["http://localhost:5173"]


settings = Settings()
