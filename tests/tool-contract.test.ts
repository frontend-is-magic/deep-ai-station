import { describe, expect, it } from 'vitest';
import {
  parseToolContractCatalog,
  parseToolContractReport,
  toolContractLessonEligible,
  type ToolContractCatalog,
  type ToolContractReport,
  type ToolContractRequest,
} from '../src/lib/tool-contract';

const runId = 'c7cdf7f8-4b8f-4e24-9c33-131b0c519b74';
const request: ToolContractRequest = {
  track: 'agent',
  lesson_id: 'agent-tool-contract',
  tool_name: 'knowledge_search',
  arguments_json: ' { "query": " MCP " } ',
};
const item = {
  id: 'agent-mcp',
  title: 'MCP 客户端与服务端',
  objective: '理解工具发现、传输与授权的分层。',
  summary: 'MCP 把工具、资源和提示暴露为协议能力。',
  source: 'https://modelcontextprotocol.io/docs',
};
function report(): ToolContractReport {
  return {
    ...request,
    contract_version: 'tool-contract-v1',
    run_id: runId,
    model_calls: 0,
    outcome: 'success',
    observation: { read_only: true, operation_id: `${runId}:1`, items: [structuredClone(item)] },
  };
}
function catalog(): ToolContractCatalog {
  return {
    contract_version: 'tool-contract-v1',
    tools: (['knowledge_search', 'lesson_read'] as const).map((name) => {
      const field = name === 'knowledge_search' ? 'query' : 'lesson_id';
      return {
        name,
        description: 'Read-only public curriculum tool.',
        parameters: {
          additionalProperties: false,
          properties: { [field]: { maxLength: 100, minLength: 1, title: 'Input', type: 'string' } },
          required: [field],
          title: name === 'knowledge_search' ? 'SearchArgs' : 'LessonArgs',
          type: 'object',
        },
      };
    }),
    supported_lesson_ids: ['agent-structured-output', 'agent-tool-contract'],
    examples: [
      { label: '合法搜索', tool_name: 'knowledge_search', arguments_json: '{"query":"MCP"}' },
      { label: '未知工具', tool_name: 'delete_file', arguments_json: '{}' },
      { label: '空参数原文', tool_name: 'lesson_read', arguments_json: '' },
    ],
    limits: { arguments_bytes: 4096, read_only: true, model_calls: 0 },
  };
}
type RecordValue = Record<string, unknown>;
const observation = (value: RecordValue) => value.observation as RecordValue;
const items = (value: RecordValue) => observation(value).items as RecordValue[];

describe('tool contract report boundaries', () => {
  it('accepts actual search results while preserving the unnormalized request identity', () => {
    const result = parseToolContractReport(report(), request);
    expect(result.arguments_json).toBe(request.arguments_json);
    expect(result.observation).toEqual(report().observation);
  });
  it('accepts an empty search as success', () => {
    const value = report();
    value.observation = { read_only: true, operation_id: `${runId}:1`, items: [] };
    expect(parseToolContractReport(value, request).outcome).toBe('success');
  });
  it('accepts a lesson read from another Agent lesson, retaining the experiment owner lesson', () => {
    const readRequest = {
      ...request,
      tool_name: 'lesson_read',
      arguments_json: '{"lesson_id":"agent-mcp"}',
    };
    const value = {
      ...report(),
      ...readRequest,
      observation: {
        read_only: true,
        operation_id: `${runId}:1`,
        lesson: {
          ...item,
          explanation: ['说明一', '说明二'],
          steps: ['读取工具 schema'],
          criteria: ['记录实际结果'],
        },
      },
    };
    expect(parseToolContractReport(value, readRequest).lesson_id).toBe('agent-tool-contract');
    value.observation.lesson.id = 'agent-tool-safety';
    expect(() => parseToolContractReport(value, readRequest)).toThrow();
  });
  it.each([
    ['invalid_arguments_json', 'knowledge_search', ''],
    ['invalid_arguments_json', 'unknown', '{'],
    ['invalid_arguments', 'knowledge_search', '{"query":1}'],
    ['invalid_arguments', 'lesson_read', '{"lesson_id":null}'],
    ['unknown_tool', '  ', '{}'],
    ['lesson_not_in_track', 'lesson_read', '{"lesson_id":"fullstack-http"}'],
  ] as const)(
    'accepts a stable rejected observation: %s / %s',
    (error, tool_name, arguments_json) => {
      const expected = { ...request, tool_name, arguments_json };
      const value = {
        ...report(),
        ...expected,
        outcome: 'rejected',
        observation: { read_only: true, operation_id: `${runId}:1`, error },
      };
      expect(parseToolContractReport(value, expected).outcome).toBe('rejected');
    },
  );
  const invalid: [string, (value: RecordValue) => void][] = [
    [
      'wrong contract version',
      (value) => {
        value.contract_version = 'tool-contract-v2';
      },
    ],
    [
      'non-v4 UUID',
      (value) => {
        value.run_id = runId.replace('-4e24-', '-3e24-');
      },
    ],
    [
      'UUID trailing newline',
      (value) => {
        value.run_id = `${runId}\n`;
      },
    ],
    [
      'wrong track',
      (value) => {
        value.track = 'fullstack';
      },
    ],
    [
      'another eligible owner lesson',
      (value) => {
        value.lesson_id = 'agent-structured-output';
      },
    ],
    [
      'another tool',
      (value) => {
        value.tool_name = 'lesson_read';
      },
    ],
    [
      'normalized instead of original input',
      (value) => {
        value.arguments_json = '{"query":"MCP"}';
      },
    ],
    [
      'nonzero model calls',
      (value) => {
        value.model_calls = 1;
      },
    ],
    [
      'unknown outcome',
      (value) => {
        value.outcome = 'partial';
      },
    ],
    [
      'extra report data',
      (value) => {
        value.internal_error = 'must not be accepted';
      },
    ],
    [
      'missing read-only guarantee',
      (value) => {
        observation(value).read_only = false;
      },
    ],
    [
      'unrelated operation ID',
      (value) => {
        observation(value).operation_id = `${runId}:2`;
      },
    ],
    [
      'success with an error',
      (value) => {
        observation(value).error = 'invalid_arguments';
      },
    ],
    [
      'rejection with successful payload',
      (value) => {
        value.outcome = 'rejected';
      },
    ],
    [
      'four results',
      (value) => {
        observation(value).items = [item, item, item, item];
      },
    ],
    [
      'duplicate result IDs',
      (value) => {
        observation(value).items = [item, item];
      },
    ],
    [
      'cross-track result ID',
      (value) => {
        items(value)[0].id = 'fullstack-http';
      },
    ],
    [
      'result ID trailing newline',
      (value) => {
        items(value)[0].id = 'agent-mcp\n';
      },
    ],
    [
      'script source URL',
      (value) => {
        items(value)[0].source = 'javascript:alert(1)';
      },
    ],
    [
      'credential-bearing source URL',
      (value) => {
        items(value)[0].source = 'https://name:pass@example.com';
      },
    ],
    [
      'missing source',
      (value) => {
        delete items(value)[0].source;
      },
    ],
    [
      'oversized summary',
      (value) => {
        items(value)[0].summary = 'x'.repeat(801);
      },
    ],
    [
      'extra course fields',
      (value) => {
        items(value)[0].private = true;
      },
    ],
    [
      'unknown error',
      (value) => {
        value.outcome = 'rejected';
        value.observation = { read_only: true, operation_id: `${runId}:1`, error: 'raw_exception' };
      },
    ],
    [
      'known tool reported as unknown',
      (value) => {
        value.outcome = 'rejected';
        value.observation = { read_only: true, operation_id: `${runId}:1`, error: 'unknown_tool' };
      },
    ],
    [
      'lesson scope error for search',
      (value) => {
        value.outcome = 'rejected';
        value.observation = {
          read_only: true,
          operation_id: `${runId}:1`,
          error: 'lesson_not_in_track',
        };
      },
    ],
  ];
  it.each(invalid)('rejects %s', (_label, mutate) => {
    const value = structuredClone(report()) as unknown as RecordValue;
    mutate(value);
    expect(() => parseToolContractReport(value, request)).toThrow('工具契约实验结果');
  });
  it.each(['{"query":""}', '{"query":1}', '{"query":"MCP","extra":1}', '[]', '{'])(
    'rejects a claimed success for invalid arguments %s',
    (arguments_json) => {
      const expected = { ...request, arguments_json };
      expect(() => parseToolContractReport({ ...report(), ...expected }, expected)).toThrow();
    },
  );
  it.each([
    ['U+FEFF retained by Python strip', '\ufeff', 'success'],
    ['U+0085 removed by Python strip', '\u0085', 'rejected'],
    ['ordinary Unicode query', '知识检索 🔎', 'success'],
  ] as const)('preserves the server outcome for %s', (_label, query, outcome) => {
    const expected = { ...request, arguments_json: JSON.stringify({ query }) };
    const value = {
      ...report(),
      ...expected,
      outcome,
      observation:
        outcome === 'success'
          ? { read_only: true, operation_id: `${runId}:1`, items: [] }
          : { read_only: true, operation_id: `${runId}:1`, error: 'invalid_arguments' },
    };
    const parsed = parseToolContractReport(value, expected);
    expect(parsed.outcome).toBe(outcome);
    expect(parsed.arguments_json).toBe(expected.arguments_json);
  });
  it('rejects claimed success for duplicate raw keys, including escaped key names', () => {
    for (const arguments_json of ['{"query":"A","query":"B"}', '{"query":"A","que\\u0072y":"B"}']) {
      const expected = { ...request, arguments_json };
      expect(() => parseToolContractReport({ ...report(), ...expected }, expected)).toThrow();
    }
  });
  it('accepts valid escaped field names and commas inside the string value', () => {
    for (const arguments_json of [
      '{"que\\u0072y":"MCP"}',
      JSON.stringify({ query: 'MCP, \"tools\"' }),
    ]) {
      const expected = { ...request, arguments_json };
      expect(parseToolContractReport({ ...report(), ...expected }, expected).outcome).toBe(
        'success',
      );
    }
  });
  it('measures argument limits in UTF-8 bytes and rejects lone surrogates', () => {
    for (const arguments_json of ['中'.repeat(1366), '\ud800', '\udc00']) {
      const expected = { ...request, arguments_json };
      const value = {
        ...report(),
        ...expected,
        outcome: 'rejected',
        observation: {
          read_only: true,
          operation_id: `${runId}:1`,
          error: 'invalid_arguments_json',
        },
      };
      expect(() => parseToolContractReport(value, expected)).toThrow();
    }
    const expected = { ...request, arguments_json: ' '.repeat(4096) };
    const value = {
      ...report(),
      ...expected,
      outcome: 'rejected',
      observation: { read_only: true, operation_id: `${runId}:1`, error: 'invalid_arguments_json' },
    };
    expect(parseToolContractReport(value, expected).arguments_json.length).toBe(4096);
  });
  it('rejects claimed success with an invalid Unicode string decoded from JSON', () => {
    const expected = { ...request, arguments_json: '{"query":"\\ud800"}' };
    expect(() => parseToolContractReport({ ...report(), ...expected }, expected)).toThrow();
  });
  it('rejects malformed lesson response arrays', () => {
    const expected = {
      ...request,
      tool_name: 'lesson_read',
      arguments_json: '{"lesson_id":"agent-mcp"}',
    };
    for (const explanation of [[], ['one', 'two', 'three'], ['x'.repeat(2001)], [null]]) {
      const value = {
        ...report(),
        ...expected,
        observation: {
          read_only: true,
          operation_id: `${runId}:1`,
          lesson: { ...item, explanation, steps: ['step'], criteria: ['criterion'] },
        },
      };
      expect(() => parseToolContractReport(value, expected)).toThrow();
    }
  });
});

describe('tool contract catalog', () => {
  it('accepts both real tool schemas and unknown-tool / invalid-JSON teaching examples', () => {
    expect(parseToolContractCatalog(catalog())).toEqual(catalog());
  });
  const invalid: [string, (value: ToolContractCatalog) => void][] = [
    [
      'unversioned catalog',
      (value) => {
        (value as unknown as RecordValue).contract_version = 'other';
      },
    ],
    [
      'duplicate tools',
      (value) => {
        value.tools[1] = value.tools[0];
      },
    ],
    [
      'external tool',
      (value) => {
        (value.tools[0] as unknown as RecordValue).name = 'fetch_url';
      },
    ],
    [
      'missing tool',
      (value) => {
        value.tools.pop();
      },
    ],
    [
      'duplicate supported lessons',
      (value) => {
        value.supported_lesson_ids[1] = value.supported_lesson_ids[0];
      },
    ],
    [
      'extra allowed arguments',
      (value) => {
        value.tools[0].parameters.additionalProperties = true;
      },
    ],
    [
      'missing required argument',
      (value) => {
        value.tools[0].parameters.required = [];
      },
    ],
    [
      'wrong argument bound',
      (value) => {
        const properties = value.tools[0].parameters.properties as RecordValue;
        (properties.query as RecordValue).maxLength = 101;
      },
    ],
    [
      'second argument property',
      (value) => {
        (value.tools[0].parameters.properties as RecordValue).url = { type: 'string' };
      },
    ],
    [
      'schema reference replacing constraints',
      (value) => {
        value.tools[0].parameters.$ref = '#other';
      },
    ],
    [
      'empty examples',
      (value) => {
        value.examples = [];
      },
    ],
    [
      'oversized multibyte example',
      (value) => {
        value.examples[0].arguments_json = '中'.repeat(1366);
      },
    ],
    [
      'different byte limit',
      (value) => {
        (value.limits as unknown as RecordValue).arguments_bytes = 8192;
      },
    ],
    [
      'write-enabled catalog',
      (value) => {
        (value.limits as unknown as RecordValue).read_only = false;
      },
    ],
    [
      'paid catalog',
      (value) => {
        (value.limits as unknown as RecordValue).model_calls = 1;
      },
    ],
  ];
  it.each(invalid)('rejects %s', (_label, mutate) => {
    const value = catalog();
    mutate(value);
    expect(() => parseToolContractCatalog(value)).toThrow('工具契约实验目录');
  });
});

describe('tool contract lesson selection', () => {
  it.each(['agent-structured-output', 'agent-tool-contract'])(
    'accepts %s from the Agent track',
    (id) => {
      expect(toolContractLessonEligible({ id, track: 'agent', title: '本课', body: [] })).toBe(
        true,
      );
    },
  );
  it.each([
    null,
    undefined,
    { id: 'agent-mcp', track: 'agent', title: 'MCP' },
    { id: 'agent-tool-contract', track: 'fullstack', title: '本课' },
    { id: 'agent-tool-contract', track: 'agent', title: '' },
  ])('rejects unsupported or malformed lesson %j', (value) => {
    expect(toolContractLessonEligible(value)).toBe(false);
  });
});
