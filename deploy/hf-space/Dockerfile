# nullMap API on Hugging Face Spaces (Docker SDK, free CPU tier).
# Spaces run the container as uid 1000, so everything the app writes lives under that user's home.
FROM python:3.12-slim

RUN useradd -m -u 1000 user
ENV HOME=/home/user \
    HF_HOME=/home/user/.cache/huggingface \
    TIKTOKEN_CACHE_DIR=/home/user/.cache/tiktoken \
    PYTHONUNBUFFERED=1
WORKDIR /home/user/app

# Dependencies come straight from pyproject.toml, including the embedding extra.
COPY pyproject.toml ./
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
 && python -c "import tomllib; p = tomllib.load(open('pyproject.toml', 'rb'))['project']; print('\n'.join(p['dependencies'] + p['optional-dependencies']['ingest']))" > requirements.txt \
 && pip install --no-cache-dir -r requirements.txt

USER user
# Bake the query embedding model into the image: no download on the first search.
# It must stay identical to the model the index was built with (EMBEDDING_MODEL).
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

COPY --chown=user app ./app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
