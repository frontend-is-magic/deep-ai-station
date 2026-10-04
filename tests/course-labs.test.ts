import { describe, expect, it } from 'vitest';
import { courseLabFor } from '../src/lib/course-labs';

describe('course lab language availability', () => {
  it('offers the write lab only for the Agent safety lesson and Python', () => {
    expect(courseLabFor('agent-tool-safety', 'python')?.id).toBe('agent-write-safety');
    expect(courseLabFor('agent-tool-safety')?.languages).toEqual(['python']);
    expect(courseLabFor('agent-tool-safety', 'go')).toBeUndefined();
    expect(courseLabFor('agent-tool-safety', 'typescript')).toBeUndefined();
    expect(courseLabFor('agent-tool-contract', 'python')).toBeUndefined();
    expect(courseLabFor('agent-research-agent', 'python')).toBeUndefined();
    expect(courseLabFor('missing-lesson', 'python')).toBeUndefined();
  });

  it('offers the MCP lab only for its Agent lesson and Python', () => {
    expect(courseLabFor('agent-mcp', 'python')?.id).toBe('mcp-readonly');
    expect(courseLabFor('agent-mcp')?.languages).toEqual(['python']);
    expect(courseLabFor('agent-mcp', 'go')).toBeUndefined();
    expect(courseLabFor('agent-mcp', 'typescript')).toBeUndefined();
    expect(courseLabFor('agent-tool-contract', 'python')).toBeUndefined();
  });

  it('offers the checkpoint lab only for the state-machine lesson and Python', () => {
    expect(courseLabFor('agent-state-machine', 'python')?.id).toBe('workflow-checkpoint');
    expect(courseLabFor('agent-state-machine')?.languages).toEqual(['python']);
    expect(courseLabFor('agent-state-machine', 'go')).toBeUndefined();
    expect(courseLabFor('agent-state-machine', 'typescript')).toBeUndefined();
    expect(courseLabFor('agent-agent-loop', 'python')).toBeUndefined();
  });

  it('offers the chunking lab only for its lesson and Python', () => {
    expect(courseLabFor('agent-chunking', 'python')?.id).toBe('document-chunking');
    expect(courseLabFor('agent-chunking')?.languages).toEqual(['python']);
    expect(courseLabFor('agent-chunking', 'go')).toBeUndefined();
    expect(courseLabFor('agent-chunking', 'typescript')).toBeUndefined();
    expect(courseLabFor('agent-rag', 'python')).toBeUndefined();
  });

  it('offers the output regression lab only for its lesson and Python', () => {
    expect(courseLabFor('agent-regression', 'python')?.id).toBe('output-regression');
    expect(courseLabFor('agent-regression')?.languages).toEqual(['python']);
    expect(courseLabFor('agent-regression', 'go')).toBeUndefined();
    expect(courseLabFor('agent-regression', 'typescript')).toBeUndefined();
    expect(courseLabFor('agent-eval-dataset', 'python')).toBeUndefined();
  });

  it('offers the memory policy lab only for its lesson and Python', () => {
    expect(courseLabFor('agent-memory', 'python')?.id).toBe('memory-policy');
    expect(courseLabFor('agent-memory')?.languages).toEqual(['python']);
    expect(courseLabFor('agent-memory', 'go')).toBeUndefined();
    expect(courseLabFor('agent-memory', 'typescript')).toBeUndefined();
    expect(courseLabFor('agent-agent-loop', 'python')).toBeUndefined();
  });

  it('retains all three language downloads for the existing fullstack labs', () => {
    for (const lesson of [
      'fullstack-routing',
      'fullstack-validation',
      'fullstack-database',
      'fullstack-migrations',
      'fullstack-auth',
      'fullstack-app-security',
      'fullstack-ai-rag',
      'fullstack-ai-stream',
      'fullstack-async',
    ]) {
      const lab = courseLabFor(lesson);
      expect(lab).toBeDefined();
      for (const language of ['typescript', 'go', 'python'] as const)
        expect(courseLabFor(lesson, language)).toBe(lab);
    }
  });
  it('maps eleven lab types, fifteen lessons and twenty-one language packages', () => {
    const lessonIds = [
      'fullstack-routing',
      'fullstack-validation',
      'fullstack-database',
      'fullstack-migrations',
      'fullstack-auth',
      'fullstack-app-security',
      'fullstack-ai-rag',
      'fullstack-ai-stream',
      'fullstack-async',
      'agent-tool-safety',
      'agent-mcp',
      'agent-state-machine',
      'agent-chunking',
      'agent-regression',
      'agent-memory',
    ];
    const labs = lessonIds.map((id) => courseLabFor(id));
    expect(labs.every((lab) => lab !== undefined)).toBe(true);
    expect(new Set(lessonIds).size).toBe(15);
    expect(new Set(labs.map((lab) => lab?.id)).size).toBe(11);
    expect(
      new Set(labs.flatMap((lab) => lab!.languages.map((language) => `${lab!.id}:${language}`)))
        .size,
    ).toBe(21);
  });
});
