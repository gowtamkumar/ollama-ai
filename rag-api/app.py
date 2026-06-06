import os
from typing import Any

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from qdrant_client import models as qmodels


OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434").rstrip("/")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333").rstrip("/")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
COLLECTION_NAME = os.environ.get("COLLECTION_NAME", "codebase")
HYBRID_ENABLED = os.environ.get("HYBRID_ENABLED", "true").lower() == "true"
SPARSE_MODEL = os.environ.get("SPARSE_MODEL", "Qdrant/bm25")
RERANK_ENABLED = os.environ.get("RERANK_ENABLED", "true").lower() == "true"
RERANK_MODEL = os.environ.get("RERANK_MODEL", "Xenova/ms-marco-MiniLM-L-6-v2")
RERANK_CANDIDATES = int(os.environ.get("RERANK_CANDIDATES", "40"))
FASTEMBED_CACHE = os.environ.get("FASTEMBED_CACHE", "/app/cache")

DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "bm25"

app = FastAPI(title="Local Code RAG API", version="2.0.0")
qdrant = QdrantClient(url=QDRANT_URL)

# Lazily-loaded optional models (sparse BM25 + cross-encoder reranker).
_sparse_model = None
_sparse_failed = False
_reranker = None
_reranker_failed = False


def get_sparse_model():
    global _sparse_model, _sparse_failed
    if not HYBRID_ENABLED or _sparse_failed:
        return None
    if _sparse_model is None:
        try:
            from fastembed import SparseTextEmbedding

            _sparse_model = SparseTextEmbedding(model_name=SPARSE_MODEL, cache_dir=FASTEMBED_CACHE)
        except Exception:
            _sparse_failed = True
            return None
    return _sparse_model


def get_reranker():
    global _reranker, _reranker_failed
    if not RERANK_ENABLED or _reranker_failed:
        return None
    if _reranker is None:
        try:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            _reranker = TextCrossEncoder(model_name=RERANK_MODEL, cache_dir=FASTEMBED_CACHE)
        except Exception:
            _reranker_failed = True
            return None
    return _reranker


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = Field(default=8, ge=1, le=50)
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    language: str | None = Field(default=None)
    path_prefix: str | None = Field(default=None)
    rerank: bool | None = Field(default=None)


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
        raise HTTPException(status_code=502, detail=f"Ollama embedding failed: {response.text}")
    embedding = response.json().get("embedding")
    if not embedding:
        raise HTTPException(status_code=502, detail="Ollama returned no embedding")
    return embedding


def sparse_embed_query(text: str):
    model = get_sparse_model()
    if model is None:
        return None
    try:
        emb = next(model.query_embed(text))
        return qmodels.SparseVector(indices=emb.indices.tolist(), values=emb.values.tolist())
    except Exception:
        return None


def build_filter(request: SearchRequest):
    conditions = []
    if request.language:
        conditions.append(
            qmodels.FieldCondition(key="language", match=qmodels.MatchValue(value=request.language))
        )
    if request.path_prefix:
        conditions.append(
            qmodels.FieldCondition(
                key="file_path", match=qmodels.MatchText(text=request.path_prefix)
            )
        )
    return qmodels.Filter(must=conditions) if conditions else None


def to_result(point) -> dict[str, Any]:
    payload = point.payload or {}
    return {
        "score": getattr(point, "score", 0.0),
        "file_path": payload.get("file_path"),
        "language": payload.get("language"),
        "start_line": payload.get("start_line"),
        "end_line": payload.get("end_line"),
        "content": payload.get("content", ""),
    }


def query_qdrant(request: SearchRequest, fetch_limit: int):
    dense_vector = embed(request.query)
    query_filter = build_filter(request)
    sparse_vector = sparse_embed_query(request.query) if HYBRID_ENABLED else None

    try:
        if sparse_vector is not None:
            response = qdrant.query_points(
                collection_name=COLLECTION_NAME,
                prefetch=[
                    qmodels.Prefetch(query=dense_vector, using=DENSE_VECTOR_NAME, limit=fetch_limit),
                    qmodels.Prefetch(query=sparse_vector, using=SPARSE_VECTOR_NAME, limit=fetch_limit),
                ],
                query=qmodels.FusionQuery(fusion=qmodels.Fusion.RRF),
                query_filter=query_filter,
                limit=fetch_limit,
                with_payload=True,
            )
            return response.points
        # Dense-only (named vector) fallback.
        response = qdrant.query_points(
            collection_name=COLLECTION_NAME,
            query=dense_vector,
            using=DENSE_VECTOR_NAME,
            query_filter=query_filter,
            score_threshold=request.score_threshold,
            limit=fetch_limit,
            with_payload=True,
        )
        return response.points
    except Exception:
        # Legacy collections (unnamed default vector) or older clients.
        try:
            if hasattr(qdrant, "search"):
                return qdrant.search(
                    collection_name=COLLECTION_NAME,
                    query_vector=dense_vector,
                    query_filter=query_filter,
                    score_threshold=request.score_threshold,
                    limit=fetch_limit,
                    with_payload=True,
                )
            response = qdrant.query_points(
                collection_name=COLLECTION_NAME,
                query=dense_vector,
                query_filter=query_filter,
                score_threshold=request.score_threshold,
                limit=fetch_limit,
                with_payload=True,
            )
            return response.points
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Qdrant search failed: {exc}") from exc


def rerank_results(query: str, results: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    reranker = get_reranker()
    if reranker is None or not results:
        return results[:limit]
    try:
        documents = [r["content"] for r in results]
        scores = list(reranker.rerank(query, documents))
        for result, score in zip(results, scores):
            result["rerank_score"] = float(score)
        results.sort(key=lambda r: r.get("rerank_score", 0.0), reverse=True)
    except Exception:
        return results[:limit]
    return results[:limit]


def search_points(request: SearchRequest) -> list[dict[str, Any]]:
    do_rerank = RERANK_ENABLED if request.rerank is None else request.rerank
    fetch_limit = max(request.limit, RERANK_CANDIDATES) if do_rerank else request.limit
    points = query_qdrant(request, fetch_limit)
    results = [to_result(p) for p in points]
    if do_rerank:
        results = rerank_results(request.query, results, request.limit)
    else:
        results = results[: request.limit]
    return results


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "hybrid": HYBRID_ENABLED,
        "rerank": RERANK_ENABLED,
        "embed_model": EMBED_MODEL,
        "collection": COLLECTION_NAME,
    }


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
