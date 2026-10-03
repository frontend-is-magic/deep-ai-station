import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import ts from 'typescript';

const examples = JSON.parse(readFileSync(process.argv[2], 'utf8'));
let count = 0;
for (const item of examples.filter((x) => x.language === 'typescript')) {
  const result = ts.transpileModule(item.source, {
    fileName: item.id + '.ts',
    reportDiagnostics: true,
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  });
  const errors = (result.diagnostics || []).filter(
    (x) => x.category === ts.DiagnosticCategory.Error,
  );
  if (errors.length) {
    throw new Error(
      item.id +
        ': ' +
        errors.map((x) => ts.flattenDiagnosticMessageText(x.messageText, '\n')).join('\n'),
    );
  }
  count++;
}
console.log(`TypeScript syntax checked: ${count}; no code executed`);

// Repository-owned references only. Temp files stay below the project so the
// compiler resolves the locked Hono/React/Jotai dependency types; no emit/run.
const parent = resolve('.tools');
mkdirSync(parent, { recursive: true });
const directory = mkdtempSync(join(parent, 'reference-types-'));
try {
  const files = examples
    .filter((item) => item.language === 'typescript')
    .map((item) => {
      if (!/^[a-z0-9-]+$/.test(item.id)) throw new Error('Invalid reference ID');
      const path = join(directory, item.id + '.ts');
      writeFileSync(path, item.source);
      return path;
    });
  const program = ts.createProgram(files, {
    noEmit: true,
    strict: true,
    skipLibCheck: true,
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.ESNext,
    moduleResolution: ts.ModuleResolutionKind.Bundler,
    moduleDetection: ts.ModuleDetectionKind.Force,
    esModuleInterop: true,
    types: ['node'],
    jsx: ts.JsxEmit.ReactJSX,
  });
  const diagnostics = ts.getPreEmitDiagnostics(program);
  if (diagnostics.length) {
    throw new Error(
      ts.formatDiagnosticsWithColorAndContext(diagnostics, {
        getCanonicalFileName: (name) => name,
        getCurrentDirectory: () => process.cwd(),
        getNewLine: () => '\n',
      }),
    );
  }
  console.log(`TypeScript strict types checked: ${files.length}; no code emitted or executed`);
} finally {
  rmSync(directory, { recursive: true, force: true });
}
