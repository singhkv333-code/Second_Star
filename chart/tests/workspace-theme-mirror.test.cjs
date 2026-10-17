// The workspace theme runtime is served from five places: its canonical
// js/ and css/ files, the root-level copies the shell loads as
// /workspace-theme.{js,css} (nginx sends those paths to this folder), and
// both Next apps' public folders. The root copies were git symlinks until
// 2026-10-16, when the release tool was found to write a symlink as a text
// file holding its target: production served the 21 bytes
// "js/workspace-theme.js" and the colour picker never loaded. They are real
// files now, so they must be kept identical by hand — this test is the check.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..', '..');
for (const [canonical, copies] of [
  ['chart/js/workspace-theme.js', ['chart/workspace-theme.js', 'app/public/workspace-theme.js', 'site/public/workspace-theme.js']],
  ['chart/css/workspace-theme.css', ['chart/workspace-theme.css', 'app/public/workspace-theme.css', 'site/public/workspace-theme.css']],
]) {
  test(`${canonical} copies are identical and not symlinks`, () => {
    const want = fs.readFileSync(path.join(root, canonical), 'utf8');
    for (const copy of copies) {
      const p = path.join(root, copy);
      assert.ok(!fs.lstatSync(p).isSymbolicLink(), `${copy} must be a real file`);
      assert.strictEqual(fs.readFileSync(p, 'utf8'), want, `${copy} differs from ${canonical}`);
    }
  });
}
