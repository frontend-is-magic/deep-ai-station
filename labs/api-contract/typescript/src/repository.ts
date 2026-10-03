import { readFileSync } from 'node:fs';

export function asciiLower(value: string): string {
  return value.replace(/[A-Z]/g, (character) => character.toLowerCase());
}

export interface Lesson {
  id: string;
  title: string;
  body: string;
}

export interface LessonRepository {
  find(id: string): Promise<Lesson | null>;
  search(question: string): Promise<Lesson[]>;
}

// Downloaded labs contain these files at their root; source checkouts share them.
// The same relative paths work from both src/ and the compiled dist/ directory.
export function readLabJson<T>(filename: 'lessons.json' | 'contract-cases.json'): T {
  let text: string;
  try {
    text = readFileSync(new URL('../' + filename, import.meta.url), 'utf8');
  } catch (error) {
    if (!(error instanceof Error) || !('code' in error) || error.code !== 'ENOENT') throw error;
    text = readFileSync(new URL('../../shared/' + filename, import.meta.url), 'utf8');
  }
  return JSON.parse(text) as T;
}

export class MemoryLessonRepository implements LessonRepository {
  private readonly lessons: Lesson[];

  constructor(lessons: readonly Lesson[] = readLabJson<Lesson[]>('lessons.json')) {
    this.lessons = lessons.map((lesson) => ({ ...lesson }));
  }

  async find(id: string): Promise<Lesson | null> {
    return this.lessons.find((lesson) => lesson.id === id) ?? null;
  }

  async search(question: string): Promise<Lesson[]> {
    const query = asciiLower(question);
    return this.lessons.filter(
      (lesson) =>
        asciiLower(lesson.title).includes(query) || asciiLower(lesson.body).includes(query),
    );
  }
}
