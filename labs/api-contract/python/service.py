"""服务层转换仓储结果；HTTP 层决定异常的状态码。"""

from repository import Lesson, Repository


class LessonNotFound(Exception):
    pass


class RepositoryUnavailable(Exception):
    pass


def summary(lesson: Lesson) -> dict[str, str]:
    # 仓储正文不会透传给浏览器。
    return {"id": lesson.id, "title": lesson.title}


class LessonService:
    def __init__(self, repository: Repository):
        self.repository = repository

    def find(self, lesson_id: str) -> dict[str, str]:
        try:
            lesson = self.repository.find(lesson_id)
        except Exception:
            raise RepositoryUnavailable from None
        if lesson is None:
            raise LessonNotFound
        return summary(lesson)

    def search(self, question: str) -> dict:
        try:
            lessons = self.repository.search(question)
        except Exception:
            raise RepositoryUnavailable from None
        return {"question": question, "items": [summary(lesson) for lesson in lessons]}
