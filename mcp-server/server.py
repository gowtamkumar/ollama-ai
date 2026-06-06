import os

import requests
from mcp.server.fastmcp import FastMCP

RAG_API_URL = os.environ.get("RAG_API_URL", "http://ai-rag-api:8011").rstrip("/")
REQUEST_TIMEOUT = int(os.environ.get("RAG_TIMEOUT", "60"))

mcp = FastMCP("local-codebase-rag")


@mcp.tool()
def search_codebase(query: str, limit: int = 8) -> str:
    """Semantic + keyword search over the locally indexed codebase.

    Call this FIRST for project analysis, codebase overview, architecture
    questions, "where is X implemented", planning, refactoring, or any coding
    task that needs repository context. Do not ask a follow-up question for
    broad requests like "analyze this project"; search the codebase and provide
    the best overview from available evidence. Returns matching code chunks
    with file paths, line ranges, and relevance scores.

    Args:
        query: Natural-language description of what you are looking for.
            Good examples: "Project overview and structure",
            "authentication flow and JWT guards", "invoice approval logic".
        limit: Maximum number of code chunks to return (1-50).
    """
    try:
        response = requests.post(
            f"{RAG_API_URL}/search",
            json={"query": query, "limit": limit},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        return f"RAG search failed: {exc}. Is the rag-api container running on the ai-net network?"

    results = response.json().get("results", [])
    if not results:
        return (
            "No matches found. The indexer may still be running, "
            "or the ./projects folder may be empty."
        )

    lines: list[str] = []
    for result in results:
        header = (
            f"### {result.get('file_path')}:"
            f"{result.get('start_line')}-{result.get('end_line')} "
            f"(score={result.get('score', 0):.3f})"
        )
        lines.append(header)
        lines.append(result.get("content", ""))
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
def get_context(query: str, limit: int = 8, max_chars: int = 9000) -> str:
    """Retrieve compact, prompt-ready context from the indexed codebase.

    Call this before edits or detailed explanations so the answer is grounded
    in real repository context. For broad project analysis, use query values
    like "Project overview and structure main modules architecture". Returns a
    single size-limited text block with file:line headers.

    Args:
        query: The question or task you need codebase context for.
        limit: Maximum number of code chunks to consider (1-50).
        max_chars: Maximum size of the returned context block (1000-30000).
    """
    try:
        response = requests.post(
            f"{RAG_API_URL}/context",
            json={"query": query, "limit": limit, "max_chars": max_chars},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        return f"RAG context failed: {exc}. Is the rag-api container running on the ai-net network?"

    context = response.json().get("context")
    return context or "No context found for this query."


if __name__ == "__main__":
    mcp.run()
