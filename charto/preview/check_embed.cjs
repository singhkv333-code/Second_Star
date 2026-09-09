const { readFileSync } = require('node:fs');
const { runInNewContext } = require('node:vm');
const assert = require('node:assert/strict');
const { join } = require('node:path');

const handlers = {};
const sent = [];
const parent = { postMessage: message => sent.push(message) };
const context = {
  window: {
    parent,
    location: { origin: 'http://localhost', search: '' },
    addEventListener: (event, callback) => { handlers[event] = callback; },
  },
  document: { readyState: 'complete', querySelector: () => ({}) },
  URLSearchParams,
  Theme: { set() {} },
};
runInNewContext(readFileSync(join(__dirname, 'js/embed.js'), 'utf8'), context);
assert.equal(sent[0].type, 'chart:ready');
const hello = { origin: 'http://localhost', source: parent, data: { type: 'pivot:hello' } };
handlers.message(hello);
assert.equal(sent[1].type, 'chart:ready', 'Parent can recover a missed ready announcement');
handlers.message({ ...hello, source: {} });
assert.equal(sent.length, 2, 'Only the embedding parent can request readiness');
context.document.querySelector = () => null;
handlers.message(hello);
assert.equal(sent[2].type, 'chart:error', 'Static HTML without a canvas is not ready');
console.log('Embed readiness checks passed');
