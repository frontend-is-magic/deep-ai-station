"""每次资料查询携带可信 owner，锁内完成查找、授权和更新。"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from threading import RLock
from typing import Protocol

from errors import LabError


class WriteForbidden(Exception):
    """授权回调拒绝写操作；不携带外部诊断信息。"""


@dataclass(frozen=True)
class Document:
    id: str
    owner: str
    title: str
    archived: bool


class Repository(Protocol):
    def list_owned(self, owner: str) -> list[Document]: ...

    def find_owned(self, owner: str, document_id: str) -> Document | None: ...

    def update_owned(
        self, owner: str, document_id: str, archived: bool, authorize: Callable[[], None]
    ) -> Document | None: ...


class MemoryRepository:
    def __init__(self, documents: list[dict]):
        self.documents = {item["id"]: Document(**item) for item in documents}
        self.lock = RLock()

    def list_owned(self, owner: str) -> list[Document]:
        with self.lock:
            return sorted(
                (item for item in self.documents.values() if item.owner == owner),
                key=lambda item: item.id,
            )

    def find_owned(self, owner: str, document_id: str) -> Document | None:
        with self.lock:
            item = self.documents.get(document_id)
            return item if item is not None and item.owner == owner else None

    def update_owned(
        self, owner: str, document_id: str, archived: bool, authorize: Callable[[], None]
    ) -> Document | None:
        with self.lock:
            item = self.find_owned(owner, document_id)
            if item is None:
                return None
            authorize()
            updated = replace(item, archived=archived)
            self.documents[document_id] = updated
            return updated


def repository_call(operation):
    try:
        return operation()
    except WriteForbidden:
        raise
    except Exception:
        raise LabError(503, "repository_unavailable") from None
