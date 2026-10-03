"""owner 配额和一次发布在同一短临界区内；内容从不写入文件系统。"""

import hashlib
from dataclasses import dataclass
from threading import RLock
from typing import Protocol

MAX_DOCUMENTS = 3
MAX_OWNER_BYTES = 8192


class QuotaExceeded(Exception):
    pass


@dataclass(frozen=True)
class StoredDocument:
    id: str
    owner: str
    filename: str
    media_type: str
    content: bytes
    sha256: str

    def metadata(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "media_type": self.media_type,
            "size_bytes": len(self.content),
            "sha256": self.sha256,
        }


class Repository(Protocol):
    def list_owned(self, owner: str) -> list[StoredDocument]: ...

    def find_owned(self, owner: str, document_id: str) -> StoredDocument | None: ...

    def commit(
        self, owner: str, filename: str, media_type: str, content: bytes
    ) -> StoredDocument: ...


class MemoryRepository:
    def __init__(self):
        self._documents: dict[str, StoredDocument] = {}
        self._lock = RLock()

    def list_owned(self, owner: str) -> list[StoredDocument]:
        with self._lock:
            return sorted(
                (item for item in self._documents.values() if item.owner == owner),
                key=lambda item: item.id,
            )

    def find_owned(self, owner: str, document_id: str) -> StoredDocument | None:
        with self._lock:
            item = self._documents.get(document_id)
            return item if item is not None and item.owner == owner else None

    def _before_publish(self) -> None:
        """可信原生测试可在发布前注入故障，不是 HTTP 配置项。"""

    def commit(self, owner: str, filename: str, media_type: str, content: bytes) -> StoredDocument:
        # bytes 将 bytearray 等调用方缓冲区复制，之后不可变。
        saved_content = bytes(content)
        digest = hashlib.sha256(saved_content).hexdigest()
        with self._lock:
            owned = [item for item in self._documents.values() if item.owner == owner]
            if (
                len(owned) >= MAX_DOCUMENTS
                or sum(len(item.content) for item in owned) + len(saved_content) > MAX_OWNER_BYTES
            ):
                raise QuotaExceeded
            document = StoredDocument(
                id=f"doc-{len(self._documents) + 1:06d}",
                owner=owner,
                filename=filename,
                media_type=media_type,
                content=saved_content,
                sha256=digest,
            )
            candidate = {**self._documents, document.id: document}
            self._before_publish()
            # 单次引用替换是唯一发布点；之前任何异常都不消耗 ID 或配额。
            self._documents = candidate
            return document
