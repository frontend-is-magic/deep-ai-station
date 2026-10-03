"""授权服务只接收 SessionStore 产生的 Principal，响应不暴露 owner。"""

from auth import Principal
from errors import LabError
from repository import Document, Repository, WriteForbidden, repository_call


def public_document(document: Document) -> dict:
    return {"id": document.id, "title": document.title, "archived": document.archived}


class DocumentService:
    def __init__(self, repository: Repository):
        self.repository = repository

    def listing(self, principal: Principal) -> dict:
        documents = repository_call(lambda: self.repository.list_owned(principal.user_id))
        return {"items": [public_document(item) for item in documents]}

    def find(self, principal: Principal, document_id: str) -> dict:
        document = repository_call(
            lambda: self.repository.find_owned(principal.user_id, document_id)
        )
        if document is None:
            raise LabError(404, "document_not_found")
        return public_document(document)

    def patch(self, principal: Principal, document_id: str, archived: bool) -> dict:
        def authorize():
            if not principal.can_write:
                raise WriteForbidden

        try:
            document = repository_call(
                lambda: self.repository.update_owned(
                    principal.user_id, document_id, archived, authorize
                )
            )
        except WriteForbidden:
            raise LabError(403, "forbidden") from None
        if document is None:
            raise LabError(404, "document_not_found")
        return public_document(document)
