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
    expect(courseLabFor('agent-memory', 'python')).toBeUndefined();
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
});
