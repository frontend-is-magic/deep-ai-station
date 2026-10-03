import { atom } from 'jotai';

export interface CodeDraft {
  context: string;
  code: string;
}

// Page-session memory only: code is separate from exportable learning records.
export const codeDraftsAtom = atom<CodeDraft[]>([]);
export function updateDraft(drafts: CodeDraft[], context: string, code: string): CodeDraft[] {
  if (code.length > 20000) return drafts;
  return [{ context, code }, ...drafts.filter((draft) => draft.context !== context)].slice(0, 20);
}
