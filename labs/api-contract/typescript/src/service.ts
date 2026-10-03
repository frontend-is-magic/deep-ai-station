import type { Lesson, LessonRepository } from './repository.js';

export class ApiError extends Error {
  constructor(
    readonly status: 404 | 413 | 415 | 422 | 503,
    readonly code: string,
  ) {
    super(code);
  }
}

function summary(lesson: Lesson) {
  return { id: lesson.id, title: lesson.title };
}

export class LessonService {
  constructor(private readonly repository: LessonRepository) {}

  async find(id: string) {
    let lesson: Lesson | null;
    try {
      lesson = await this.repository.find(id);
    } catch {
      throw new ApiError(503, 'repository_unavailable');
    }
    if (!lesson) throw new ApiError(404, 'lesson_not_found');
    return summary(lesson);
  }

  async search(input: unknown) {
    if (
      input === null ||
      typeof input !== 'object' ||
      Array.isArray(input) ||
      Object.keys(input).length !== 1 ||
      !('question' in input) ||
      typeof input.question !== 'string'
    ) {
      throw new ApiError(422, 'invalid_input');
    }
    // Unicode White_Space differs from JavaScript trim(): NEL is whitespace; BOM is not.
    const question = input.question.replace(/^\p{White_Space}+|\p{White_Space}+$/gu, '');
    const length = Array.from(question).length;
    if (length < 1 || length > 500) throw new ApiError(422, 'invalid_input');
    for (const character of question) {
      const codePoint = character.codePointAt(0)!;
      if (codePoint >= 0xd800 && codePoint <= 0xdfff) throw new ApiError(422, 'invalid_input');
    }
    try {
      const lessons = await this.repository.search(question);
      return { question, items: lessons.map(summary) };
    } catch {
      throw new ApiError(503, 'repository_unavailable');
    }
  }
}
