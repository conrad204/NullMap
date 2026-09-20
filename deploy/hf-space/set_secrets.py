"""Copy the backend's settings into a Hugging Face Space. Values are never printed.

usage (from the repo root):
    backend/.venv/bin/python deploy/hf-space/set_secrets.py <hf-username>/nullmap-api https://your-app.vercel.app
"""

import json
import sys
from pathlib import Path

from huggingface_hub import HfApi

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.config import settings


def main() -> None:
    if len(sys.argv) < 3 or not all(o.startswith("https://") for o in sys.argv[2:]):
        raise SystemExit(__doc__)
    space, origins = sys.argv[1], [o.rstrip("/") for o in sys.argv[2:]]
    secrets = {
        "ELASTIC_URL": settings.elastic_url,
        "ELASTIC_API_KEY": settings.elastic_api_key,
        "OPENAI_API_KEY": settings.openai_api_key,
    }
    missing = [name for name, value in secrets.items() if not value or "localhost" in value]
    if missing:
        raise SystemExit(f"backend/.env has no usable value for: {', '.join(missing)}")
    api = HfApi()
    for name, value in secrets.items():
        api.add_space_secret(space, name, value)
        print(f"secret   {name} set")
    variables = {"ELASTIC_INDEX": settings.elastic_index, "CORS_ORIGINS": json.dumps(origins)}
    for name, value in variables.items():
        api.add_space_variable(space, name, value)
        print(f"variable {name} = {value}")
    print("Done. The Space restarts on its own to pick these up.")


if __name__ == "__main__":
    main()
