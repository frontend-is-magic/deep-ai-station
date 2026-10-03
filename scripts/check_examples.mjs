import { readFileSync } from 'node:fs';
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
