import { MAX_INPUT_BYTES, LabError, parseCommand, publicError } from './request.js';
import { Repository } from './repository.js';

async function main(): Promise<unknown> {
  if (process.argv.length !== 3 || process.argv[2].length === 0) {
    throw new LabError('invalid_input');
  }
  const chunks: Buffer[] = [];
  let length = 0;
  for await (const chunk of process.stdin) {
    const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    length += bytes.byteLength;
    if (length > MAX_INPUT_BYTES) throw new LabError('invalid_input');
    chunks.push(bytes);
  }
  const command = parseCommand(Buffer.concat(chunks));
  const repository = new Repository(process.argv[2]);
  try {
    return repository.execute(command);
  } finally {
    repository.close();
  }
}

try {
  process.stdout.write(`${JSON.stringify(await main())}\n`);
} catch (error) {
  process.stdout.write(`${JSON.stringify({ error: publicError(error) })}\n`);
  process.exitCode = 1;
}
