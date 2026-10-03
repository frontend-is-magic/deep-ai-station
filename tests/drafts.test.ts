import { expect, it } from 'vitest';
import { updateDraft } from '../src/lib/drafts';

it('separates course/language edits and preserves an intentionally empty editor', () => {
  const first = updateDraft([], 'fullstack:fullstack-http:go', 'package main');
  const second = updateDraft(first, 'fullstack:fullstack-http:python', 'print(17)');
  const empty = updateDraft(second, 'fullstack:fullstack-http:go', '');
  expect(empty.find((draft) => draft.context.endsWith(':go'))?.code).toBe('');
  expect(empty.find((draft) => draft.context.endsWith(':python'))?.code).toBe('print(17)');
  expect(empty).toHaveLength(2);
});

it('keeps the 20 most recently edited contexts without accepting oversized code', () => {
  let drafts = Array.from({ length: 20 }, (_, i) => ({ context: `course-${i}`, code: String(i) }));
  drafts = updateDraft(drafts, 'course-19', 'recently edited');
  drafts = updateDraft(drafts, 'new-course', 'new code');
  expect(drafts).toHaveLength(20);
  expect(drafts.some((draft) => draft.context === 'course-19')).toBe(true);
  expect(drafts.some((draft) => draft.context === 'course-18')).toBe(false);
  expect(updateDraft(drafts, 'new-course', 'x'.repeat(20001))).toBe(drafts);
});
