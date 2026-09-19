from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    elastic_url: str = "http://localhost:9200"
    elastic_api_key: str = ""
    openai_api_key: str = ""
    openalex_api_key: str = ""
    hf_token: str = ""
    cors_origins: list[str] = ["http://localhost:5173"]


settings = Settings()
