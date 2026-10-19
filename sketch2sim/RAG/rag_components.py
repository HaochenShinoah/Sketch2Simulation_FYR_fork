"""Retrieval over the HYSYS pure-component list and the mixture recipes.

The Basis Agent asks this module for the HYSYS component names that match a
feed material (for example ``"Crude oil"`` or ``"Fresh water"``). Retrieval is
hybrid, as in the original work:

1. exact lookup over the key fields (HYSYS name, full name, formula, mixture
   name and aliases), then
2. vector similarity over OpenAI text embeddings (``S2S_EMBEDDING_MODEL``,
   default ``text-embedding-3-small``).

Document embeddings are computed once and cached under ``RAG/cache/``; the
cache key covers the embedding model and the full document text, so editing
``components_list.xlsx`` or ``mixture_recipes.txt`` rebuilds it automatically.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from sketch2sim import config, llm

RAG_DIR = Path(__file__).resolve().parent
DEFAULT_EXCEL_PATH = RAG_DIR / "components_list.xlsx"
DEFAULT_MIXTURE_PATH = RAG_DIR / "mixture_recipes.txt"
DEFAULT_CACHE_DIR = RAG_DIR / "cache"


@dataclass
class Document:
    page_content: str
    metadata: dict[str, Any] = field(default_factory=dict)


def load_pure_component_docs_from_excel(
    excel_path: str | Path = DEFAULT_EXCEL_PATH,
) -> list[Document]:
    excel_path = Path(excel_path)
    if not excel_path.exists():
        raise FileNotFoundError(f"Excel file not found: {excel_path}")

    df = pd.read_excel(excel_path)

    required_cols = {"component_name", "full_name", "formula"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"components_list.xlsx is missing required columns: {missing}")

    docs: list[Document] = []
    for _, row in df.iterrows():
        name = str(row["component_name"]).strip()
        full_name = str(row["full_name"]).strip()
        formula = str(row["formula"]).strip()

        text = (
            "Type: pure_component\n"
            f"HYSYS_name: {name}\n"
            f"Full_name: {full_name}\n"
            f"Formula: {formula}\n"
            f"Aliases: {name}, {full_name}, {formula}\n"
        )
        docs.append(
            Document(
                page_content=text,
                metadata={
                    "kind": "pure_component",
                    "component_name": name,
                    "full_name": full_name,
                    "formula": formula,
                },
            )
        )
    return docs


def load_mixture_recipe_docs(
    txt_path: str | Path = DEFAULT_MIXTURE_PATH,
) -> list[Document]:
    txt_path = Path(txt_path)
    if not txt_path.exists():
        raise FileNotFoundError(f"Mixture recipe file not found: {txt_path}")

    raw_text = txt_path.read_text(encoding="utf-8")
    blocks = [
        block.strip()
        for block in raw_text.split("========================================")
        if block.strip()
    ]

    docs: list[Document] = []
    for block in blocks:
        mixture_name = None
        for line in block.splitlines():
            line = line.strip()
            if line.lower().startswith("mixture_name:"):
                mixture_name = line.split(":", 1)[1].strip()
                break
        docs.append(
            Document(
                page_content=block,
                metadata={
                    "kind": "pseudo_recipe",
                    "mixture_name": mixture_name or "UNKNOWN_MIXTURE",
                },
            )
        )
    return docs


class EmbeddingIndex:
    """In-memory cosine-similarity index over OpenAI embeddings."""

    def __init__(
        self, docs: list[Document], vectors: np.ndarray, model: str, k: int = 2
    ):
        if len(docs) != vectors.shape[0]:
            raise ValueError("docs and vectors must have the same length")
        self.docs = docs
        self.model = model
        self.k = k
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self.vectors = (vectors / norms).astype(np.float32)

    @staticmethod
    def _cache_key(docs: list[Document], model: str) -> str:
        digest = hashlib.sha256()
        digest.update(model.encode("utf-8"))
        for doc in docs:
            digest.update(b"\x00")
            digest.update(doc.page_content.encode("utf-8"))
        return digest.hexdigest()[:24]

    @classmethod
    def build(
        cls,
        docs: list[Document],
        model: str,
        cache_dir: str | Path | None = DEFAULT_CACHE_DIR,
        k: int = 2,
    ) -> EmbeddingIndex:
        cache_file: Path | None = None
        if cache_dir is not None:
            cache_dir = Path(cache_dir)
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = cache_dir / f"embeddings_{cls._cache_key(docs, model)}.npy"
            if cache_file.exists():
                vectors = np.load(cache_file)
                if vectors.shape[0] == len(docs):
                    return cls(docs, vectors, model, k=k)

        print(
            f"[RAG] Embedding {len(docs)} documents with {model} "
            "(cached for later runs)..."
        )
        vectors = np.asarray(
            llm.embed_texts([doc.page_content for doc in docs], model=model),
            dtype=np.float32,
        )
        if cache_file is not None:
            np.save(cache_file, vectors)
        return cls(docs, vectors, model, k=k)

    def search(self, query: str, k: int | None = None) -> list[Document]:
        k = k or self.k
        query_vec = np.asarray(
            llm.embed_texts([query], model=self.model)[0], dtype=np.float32
        )
        norm = np.linalg.norm(query_vec)
        if norm:
            query_vec = query_vec / norm
        scores = self.vectors @ query_vec
        top = np.argsort(-scores)[:k]
        return [self.docs[int(index)] for index in top]

    def invoke(self, query: str, **kwargs) -> list[Document]:
        return self.search(query, k=kwargs.get("k"))


class ExactThenVectorRetriever:
    """
    Hybrid retriever:
    1. Exact lookup over key fields
    2. Fall back to vector retrieval if no exact hit
    """

    def __init__(self, vector_retriever, docs: list[Document]):
        self.vector_retriever = vector_retriever
        self._exact_index: dict[str, list[Document]] = {}
        self._build_exact_index(docs)

    @staticmethod
    def _norm(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip().lower())

    @staticmethod
    def _field(text: str, field_name: str) -> str:
        match = re.search(
            rf"(?im)^\s*{re.escape(field_name)}\s*:\s*(.+?)\s*$", text or ""
        )
        return match.group(1).strip() if match else ""

    def _aliases(self, text: str) -> list[str]:
        aliases = self._field(text, "Aliases")
        if aliases:
            return [alias.strip() for alias in aliases.split(",") if alias.strip()]
        # Mixture recipes list aliases as indented "- name" lines.
        match = re.search(
            r"(?is)^\s*Aliases:\s*\n((?:\s*-\s*.+\n?)+)", text or "", re.M
        )
        if not match:
            return []
        return [
            line.strip().lstrip("-").strip()
            for line in match.group(1).splitlines()
            if line.strip().lstrip("-").strip()
        ]

    def _add_key(self, key: str, doc: Document) -> None:
        normalized_key = self._norm(key)
        if not normalized_key:
            return
        self._exact_index.setdefault(normalized_key, []).append(doc)

    def _build_exact_index(self, docs: list[Document]) -> None:
        for doc in docs:
            text = doc.page_content or ""
            self._add_key(self._field(text, "HYSYS_name"), doc)
            self._add_key(self._field(text, "Full_name"), doc)
            self._add_key(self._field(text, "Formula"), doc)
            self._add_key(self._field(text, "Mixture_name"), doc)
            for alias in self._aliases(text):
                self._add_key(alias, doc)

    @staticmethod
    def _dedupe_keep_order(docs: list[Document]) -> list[Document]:
        seen = set()
        unique_docs: list[Document] = []
        for doc in docs:
            key = (doc.page_content or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            unique_docs.append(doc)
        return unique_docs

    def invoke(self, query: str, **kwargs) -> list[Document]:
        normalized_query = self._norm(query)
        exact_matches = (
            self._exact_index.get(normalized_query, []) if normalized_query else []
        )
        vector_matches = self.vector_retriever.invoke(query, **kwargs) or []
        if exact_matches:
            return self._dedupe_keep_order(exact_matches + vector_matches)
        return self._dedupe_keep_order(vector_matches)


def build_component_retriever(
    excel_path: str | Path | None = None,
    mixture_path: str | Path | None = None,
    persist_dir: str | Path | None = DEFAULT_CACHE_DIR,
    k: int = 2,
    embedding_model: str | None = None,
) -> ExactThenVectorRetriever:
    """Build the hybrid retriever used by the Basis Agent.

    ``persist_dir`` is where the embedding cache lives (``None`` disables the
    cache). Paths default to the files shipped next to this module, so the
    pipeline no longer depends on the current working directory.
    """
    excel_path = Path(excel_path) if excel_path else DEFAULT_EXCEL_PATH
    mixture_path = Path(mixture_path) if mixture_path else DEFAULT_MIXTURE_PATH
    embedding_model = embedding_model or config.EMBEDDING_MODEL

    pure_docs = load_pure_component_docs_from_excel(excel_path)
    mixture_docs = load_mixture_recipe_docs(mixture_path)
    all_docs = pure_docs + mixture_docs
    if not all_docs:
        raise ValueError("No documents loaded for RAG index.")

    index = EmbeddingIndex.build(all_docs, embedding_model, cache_dir=persist_dir, k=k)
    return ExactThenVectorRetriever(index, all_docs)
