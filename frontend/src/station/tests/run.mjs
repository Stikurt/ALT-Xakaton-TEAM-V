import { spawnSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
mkdirSync('test-results', { recursive: true });
const compiled = spawnSync(process.execPath, ['node_modules/typescript/bin/tsc', '--project', 'tests/tsconfig.json'], { stdio: 'inherit' });
if (compiled.status !== 0)
    process.exit(compiled.status ?? 1);
writeFileSync('test-results/package.json', JSON.stringify({ type: 'commonjs' }));
const result = spawnSync(process.execPath, ['--test', 'test-results/tests/geometry.test.js'], { stdio: 'inherit' });
process.exit(result.status ?? 1);
