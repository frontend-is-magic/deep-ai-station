"""应用服务只使用认证后的 owner，并隔离仓储内部诊断。"""

from auth import Principal
from errors import LabError
from repository import QuotaExceeded, Repository


def repository_call(operation):
    try:
        return operation()
    except QuotaExceeded:
        raise LabError(409, "quota_exceeded") from None
    except Exception:
        raise LabError(503, "repository_unavailable") from None


class DocumentService:
    def __init__(self, repository: Repository):
        self.repository = repository

    def listing(self, principal: Principal) -> dict:
        documents = repository_call(lambda: self.repository.list_owned(principal.user_id))
        return {"documents": [document.metadata() for document in documents]}

    def find(self, principal: Principal, document_id: str):
        document = repository_call(
            lambda: self.repository.find_owned(principal.user_id, document_id)
        )
        if document is None:
            raise LabError(404, "document_not_found")
        return document

    def upload(self, principal: Principal, filename: str, media_type: str, content: bytes):
        return repository_call(
            lambda: self.repository.commit(principal.user_id, filename, media_type, content)
        ).metadata()
