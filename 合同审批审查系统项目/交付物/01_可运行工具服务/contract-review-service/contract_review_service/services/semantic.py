"""BGE-M3 向量化 + Milvus 手册语义检索。

- 模型和 Milvus 客户端都按需加载，未启用 Milvus 时不会加载模型。
- 手册切片文本存在 Milvus 的 text 字段，page 字段用于回指页码。
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

# Windows + 新版 tokenizers 并行编码会死锁，必须在导入 sentence-transformers 前设置。
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

from contract_review_service.common.config import get_settings

logger = logging.getLogger(__name__)

DIMENSION = 1024
_embedder: Any | None = None
_client: Any | None = None
_lock = threading.Lock()


def get_embedder() -> Any:
    """懒加载 BGE-M3，避免未启用语义检索时占用内存。"""
    global _embedder
    if _embedder is None:
        with _lock:
            if _embedder is None:
                settings = get_settings()
                if not settings.bge_m3_path:
                    raise RuntimeError("CONTRACT_BGE_M3_PATH 未配置")
                import torch
                from sentence_transformers import SentenceTransformer

                dtype_map = {
                    "float32": torch.float32,
                    "fp32": torch.float32,
                    "float16": torch.float16,
                    "fp16": torch.float16,
                    "bfloat16": torch.bfloat16,
                    "bf16": torch.bfloat16,
                }
                dtype = dtype_map.get(settings.bge_dtype.lower(), torch.bfloat16)
                logger.info(
                    "加载 BGE-M3：%s（device=%s, dtype=%s）",
                    settings.bge_m3_path,
                    settings.bge_device,
                    dtype,
                )
                _embedder = SentenceTransformer(
                    settings.bge_m3_path,
                    device=settings.bge_device or "cpu",
                    local_files_only=True,
                    model_kwargs={
                        "dtype": dtype,
                        "low_cpu_mem_usage": True,
                    },
                )
    return _embedder


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    settings = get_settings()
    vectors = get_embedder().encode(
        texts,
        batch_size=settings.bge_batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return [vector.tolist() for vector in vectors]


def get_client() -> Any:
    """懒加载 Milvus 客户端。"""
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                settings = get_settings()
                if not settings.milvus_url:
                    raise RuntimeError("CONTRACT_MILVUS_URL 未配置")
                from pymilvus import MilvusClient

                logger.info("连接 Milvus：%s", settings.milvus_url)
                _client = MilvusClient(uri=settings.milvus_url)
    return _client


def ensure_collection(*, reset: bool = False) -> Any:
    """创建手册切片集合（1024 维、COSINE），返回 Milvus 客户端。"""
    from pymilvus import DataType

    settings = get_settings()
    client = get_client()
    name = settings.milvus_collection

    if reset and client.has_collection(name):
        client.drop_collection(name)

    if not client.has_collection(name):
        schema = client.create_schema(auto_id=True, enable_dynamic_field=False)
        schema.add_field("id", DataType.INT64, is_primary=True, auto_id=True)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=DIMENSION)
        schema.add_field("text", DataType.VARCHAR, max_length=8192)
        schema.add_field("page", DataType.INT64)
        schema.add_field("source", DataType.VARCHAR, max_length=512)
        schema.add_field("chunk_index", DataType.INT64)

        index_params = client.prepare_index_params()
        index_params.add_index(
            field_name="vector",
            index_type="AUTOINDEX",
            metric_type="COSINE",
        )
        client.create_collection(
            collection_name=name,
            schema=schema,
            index_params=index_params,
        )
        logger.info("已创建 Milvus 集合：%s", name)

    client.load_collection(name)
    return client


def index_chunks(
    chunks: list[dict[str, Any]],
    *,
    batch_size: int | None = None,
    reset: bool = False,
) -> int:
    """把手册切片写入 Milvus。chunk 需要 text/page/source/chunk_index。"""
    if not chunks:
        return 0
    settings = get_settings()
    client = ensure_collection(reset=reset)
    name = settings.milvus_collection
    size = batch_size or max(1, settings.bge_batch_size * 2)

    for start in range(0, len(chunks), size):
        batch = chunks[start:start + size]
        vectors = embed_texts([item["text"] for item in batch])
        data = [
            {
                "vector": vector,
                "text": item["text"],
                "page": int(item.get("page", 0)),
                "source": str(item.get("source", "")),
                "chunk_index": int(item.get("chunk_index", start + offset)),
            }
            for offset, (item, vector) in enumerate(zip(batch, vectors))
        ]
        client.insert(collection_name=name, data=data)
        logger.info("已写入 %s 条切片（%s/%s）", len(data), start + len(batch), len(chunks))

    try:
        client.flush(name)
    except Exception:
        logger.warning("Milvus flush 失败，检索可能短暂延迟", exc_info=True)
    return len(chunks)


def search_manual(
    query: str,
    *,
    top_k: int | None = None,
    threshold: float | None = None,
) -> list[dict[str, Any]]:
    """检索手册参考片段。Milvus 未就绪时返回空列表，由调用方降级。"""
    settings = get_settings()
    if not settings.milvus_enabled:
        return []

    client = get_client()
    name = settings.milvus_collection
    if not client.has_collection(name):
        logger.warning("Milvus 集合不存在：%s，请先运行 scripts/import_manual.py", name)
        return []

    client.load_collection(name)
    vector = embed_texts([query])[0]
    results = client.search(
        collection_name=name,
        data=[vector],
        limit=top_k or settings.semantic_top_k,
        output_fields=["text", "page", "source", "chunk_index"],
        search_params={"metric_type": "COSINE"},
    )

    hits: list[dict[str, Any]] = []
    min_score = settings.semantic_score_threshold if threshold is None else threshold
    for item in results[0]:
        score = float(item.get("distance", 0.0))
        if score < min_score:
            continue
        entity = item.get("entity", {})
        hits.append(
            {
                "score": score,
                "text": entity.get("text", ""),
                "page": int(entity.get("page", 0)),
                "source": entity.get("source", ""),
                "chunk_index": int(entity.get("chunk_index", 0)),
            }
        )
    return hits
