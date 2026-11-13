import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const workflow = readFileSync(new URL('../.github/workflows/ci.yml', import.meta.url), 'utf8');
const pyproject = readFileSync(new URL('../runtime/pyproject.toml', import.meta.url), 'utf8');

test('Python runtime CI installs only the dedicated required test dependency', () => {
  assert.match(pyproject, /\[project\.optional-dependencies\][\s\S]*?test = \[\s*"jsonschema>=4\.23,<5"\s*\]/);
  assert.equal((workflow.match(/pip install -e "\.\/runtime\[test\]"/g) || []).length, 2);
  assert.doesNotMatch(workflow, /pip install -e "\.\/runtime\[dev\]"/);
});

test('developers retain the full optional lint and coverage tooling', () => {
  const dev = pyproject.match(/dev = \[([\s\S]*?)\]/)?.[1] || '';
  for (const requirement of ['coverage>=7,<8', 'jsonschema>=4.23,<5', 'mypy>=1.14,<2', 'ruff>=0.9,<1']) {
    assert.ok(dev.includes(`"${requirement}"`), `missing developer extra ${requirement}`);
  }
});

test('both isolated runtime test matrices and original trust gates remain present', () => {
  assert.match(workflow, /runtime:\n[\s\S]*?matrix:\n\s+python: \["3\.10", "3\.12"\]/);
  assert.equal((workflow.match(/-m unittest discover -s tests -p 'test_\*\.py'/g) || []).length, 2);
  assert.match(workflow, /poststeward-verification/);
  assert.match(workflow, /python -m venv "\$RUNNER_TEMP\/poststeward-runtime-matrix"/);
});
