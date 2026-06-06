import os
from typing import Any

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient


OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434").rstrip("/")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333").rstrip("/")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
COLLECTION_NAME = os.environ.get("COLLECTION_NAME", "codebase")

app = FastAPI(title="Local Code RAG API", version="1.0.0")
qdrant = QdrantClient(url=QDRANT_URL)


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = Field(default=8, ge=1, le=50)
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class ContextRequest(SearchRequest):
    max_chars: int = Field(default=9000, ge=1000, le=30000)


def embed(text: str) -> list[float]:
    try:
        response = requests.post(
            f"{OLLAMA_URL}/api/embed",
            json={"model": EMBED_MODEL, "input": text},
            timeout=30,
        )
        if response.status_code == 200:
            data = response.json()
            embeddings = data.get("embeddings")
            if embeddings:
                return embeddings[0]
            embedding = data.get("embedding")
            if embedding:
                return embedding
    except requests.RequestException:
        pass

    response = requests.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=30,
    )
    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Ollama embedding failed: {response.text}",
        )

    embedding = response.json().get("embedding")
    if not embedding:
        raise HTTPException(status_code=502, detail="Ollama returned no embedding")
    return embedding


def search_points(request: SearchRequest) -> list[dict[str, Any]]:
    vector = embed(request.query)
    try:
        points = qdrant.search(
            collection_name=COLLECTION_NAME,
            query_vector=vector,
            limit=request.limit,
            score_threshold=request.score_threshold,
            with_payload=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Qdrant search failed: {exc}") from exc

    results: list[dict[str, Any]] = []
    for point in points:
        payload = point.payload or {}
        results.append(
            {
                "score": point.score,
                "file_path": payload.get("file_path"),
                "start_line": payload.get("start_line"),
                "end_line": payload.get("end_line"),
                "content": payload.get("content", ""),
            }
        )
    return results


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/search")
def search(request: SearchRequest) -> dict[str, Any]:
    return {"query": request.query, "results": search_points(request)}


@app.post("/context")
def context(request: ContextRequest) -> dict[str, Any]:
    results = search_points(request)
    blocks: list[str] = []
    used_chars = 0

    for result in results:
        header = f"{result['file_path']}:{result['start_line']}-{result['end_line']}"
        block = f"---\n{header}\n{result['content']}\n"
        if used_chars + len(block) > request.max_chars:
            break
        blocks.append(block)
        used_chars += len(block)

    return {
        "query": request.query,
        "context": "\n".join(blocks),
        "results": results[: len(blocks)],
    }
