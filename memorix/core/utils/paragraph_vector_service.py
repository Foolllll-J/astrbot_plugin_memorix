"""Paragraph vector write/backfill helpers.

段落元数据是主数据源；向量写入失败不应回滚段落，后台回填会补齐缺失向量。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ...amemorix.common.logging import get_logger

logger = get_logger("A_Memorix.ParagraphVectorService")


@dataclass
class ParagraphVectorWriteResult:
    hash_value: str
    vector_written: bool
    vector_already_exists: bool
    vector_state: str
    error: str = ""


class ParagraphVectorWriteService:
    """段落向量写入收口服务。"""

    READY_MARKER = 1
    FAILED_MARKER = -1
    ERROR_MAX_LEN = 500

    def __init__(
        self,
        metadata_store: Any,
        vector_store: Any,
        embedding_manager: Any,
    ):
        self.metadata_store = metadata_store
        self.vector_store = vector_store
        self.embedding_manager = embedding_manager

    def _mark_vector_index(self, hash_value: str, marker: int) -> None:
        updater = getattr(self.metadata_store, "update_vector_index", None)
        if not callable(updater):
            return
        try:
            updater("paragraph", hash_value, int(marker))
        except Exception as exc:
            logger.debug("mark paragraph vector state failed: hash=%s err=%s", hash_value[:16], exc)

    async def ensure_paragraph_vector(
        self,
        hash_value: str,
        content: str,
        *,
        max_error_len: int = ERROR_MAX_LEN,
    ) -> ParagraphVectorWriteResult:
        """确保指定段落向量存在；失败只标记状态，不抛出到写入主链路。"""
        token = str(hash_value or "").strip()
        text = str(content or "").strip()
        if not token or not text:
            return ParagraphVectorWriteResult(
                hash_value=token,
                vector_written=False,
                vector_already_exists=False,
                vector_state="skipped",
                error="empty_hash_or_content",
            )

        if token in self.vector_store:
            self._mark_vector_index(token, self.READY_MARKER)
            return ParagraphVectorWriteResult(
                hash_value=token,
                vector_written=False,
                vector_already_exists=True,
                vector_state="ready",
            )

        try:
            embedding = await self.embedding_manager.encode(text)
            if getattr(embedding, "ndim", 1) == 1:
                embedding = embedding.reshape(1, -1)
            self.vector_store.add(vectors=embedding, ids=[token])
            self._mark_vector_index(token, self.READY_MARKER)
            logger.info(
                "metric.paragraph_vector_write_success=1 metric.paragraph_vector_write_success_count=1 hash=%s",
                token[:16],
            )
            return ParagraphVectorWriteResult(
                hash_value=token,
                vector_written=True,
                vector_already_exists=False,
                vector_state="ready",
            )
        except ValueError:
            # VectorStore.add 对已存在 ID 可能被不同实现抛为 ValueError；按已就绪处理。
            self._mark_vector_index(token, self.READY_MARKER)
            return ParagraphVectorWriteResult(
                hash_value=token,
                vector_written=False,
                vector_already_exists=True,
                vector_state="ready",
            )
        except Exception as exc:
            err = str(exc)[:max_error_len]
            self._mark_vector_index(token, self.FAILED_MARKER)
            logger.warning(
                "metric.paragraph_vector_write_fail=1 metric.paragraph_vector_write_fail_count=1 hash=%s err=%s",
                token[:16],
                err,
            )
            return ParagraphVectorWriteResult(
                hash_value=token,
                vector_written=False,
                vector_already_exists=False,
                vector_state="failed",
                error=err,
            )

    async def backfill_missing_vectors(
        self,
        *,
        batch_size: int = 50,
        scan_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """扫描 vector_index 未标记就绪的段落，补写缺失向量。"""
        safe_batch = max(1, int(batch_size or 50))
        safe_scan = max(safe_batch, int(scan_limit or safe_batch * 20))
        lister = getattr(self.metadata_store, "list_paragraphs_for_vector_backfill", None)
        if callable(lister):
            rows = lister(limit=safe_scan)
        else:
            rows = self._fallback_list_candidates(limit=safe_scan)

        processed = 0
        written = 0
        already_exists = 0
        failed: List[Dict[str, str]] = []
        skipped = 0

        for row in rows:
            if processed >= safe_batch:
                break
            hash_value = str(row.get("hash", "") or "").strip()
            content = str(row.get("content", "") or "")
            if not hash_value or not content.strip():
                skipped += 1
                continue
            result = await self.ensure_paragraph_vector(hash_value, content)
            processed += 1
            if result.vector_written:
                written += 1
            elif result.vector_already_exists:
                already_exists += 1
            elif result.vector_state == "failed":
                failed.append({"hash": hash_value, "error": result.error})

        return {
            "scanned": len(rows),
            "processed": processed,
            "written": written,
            "already_exists": already_exists,
            "failed": failed,
            "skipped": skipped,
        }

    def _fallback_list_candidates(self, *, limit: int) -> List[Dict[str, Any]]:
        query = getattr(self.metadata_store, "query", None)
        if not callable(query):
            return []
        rows = query(
            """
            SELECT hash, content, source, vector_index, created_at, updated_at
            FROM paragraphs
            WHERE (is_deleted IS NULL OR is_deleted = 0)
              AND (vector_index IS NULL OR vector_index < 0)
            ORDER BY COALESCE(updated_at, created_at, 0) ASC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        )
        return [dict(row) for row in rows]
