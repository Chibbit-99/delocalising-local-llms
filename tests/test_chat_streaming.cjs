// Run with: node tests/test_chat_streaming.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const index = fs.readFileSync('index.html', 'utf8');
const workflow = fs.readFileSync('.github/workflows/llm-test.yml', 'utf8');
const htmlScript = index.match(/<script>\n([\s\S]*?)\n<\/script>/)[1];
const workflowScript = workflow.split('          <script>\n')[1].split('          </script>')[0]
  .split('\n').map(line => line.startsWith('          ') ? line.slice(10) : line).join('\n').trimEnd();
// Apart from endpoint/model configuration, the published template and page must match.
assert.equal(workflowScript.split('\n').slice(1).join('\n'), htmlScript.split('\n').slice(1).join('\n'));

const handlers = {};
const items = [];
const form = {addEventListener: (type, cb) => { handlers[type] = cb; }};
const promptBox = {value: '', addEventListener() {}, focus() {}};
const send = {disabled: false};
const status = {};
const messages = {appendChild(el) { items.push(el); }};
const elements = {'#chat': form, '#prompt': promptBox, '#send': send, '#status': status, '#messages': messages};
const document = {
  querySelector(selector) { return elements[selector] || null; },
  createElement() { return {scrollIntoView() {}}; }
};
const requests = [];
let nextResponse;
async function fetch(url, options) {
  if (url.endsWith('/api/tags')) return {ok: true};
  requests.push(JSON.parse(options.body));
  return nextResponse;
}
vm.runInNewContext(htmlScript, {document, fetch, TextDecoder, Error, JSON});
const tick = () => new Promise(resolve => setImmediate(resolve));
const encoder = new TextEncoder();
function streamResponse() {
  let controller;
  const body = new ReadableStream({start(c) { controller = c; }});
  return {response: {ok: true, body}, controller};
}

(async () => {
  const first = streamResponse();
  nextResponse = first.response;
  promptBox.value = 'Hello';
  const pending = handlers.submit({preventDefault() {}});
  await tick();
  assert.equal(requests[0].stream, true);
  assert.equal(send.disabled, true);
  // Split a UTF-8 character across network chunks and then pause the stream.
  const part = encoder.encode('{"message":{"content":"Hé"}}\n');
  const accent = part.indexOf(0xc3);
  first.controller.enqueue(part.slice(0, accent + 1));
  await tick();
  assert.equal(items.at(-1).textContent, 'Thinking…');
  first.controller.enqueue(part.slice(accent + 1));
  await tick();
  assert.equal(items.at(-1).textContent, 'Hé'); // before the upstream finishes
  first.controller.enqueue(encoder.encode('{"message":{"content":"llo"}}\n{"done":true}'));
  first.controller.close();
  await pending;
  assert.equal(items.at(-1).textContent, 'Héllo');
  assert.equal(send.disabled, false);

  const interrupted = streamResponse();
  nextResponse = interrupted.response;
  promptBox.value = 'Again';
  const failed = handlers.submit({preventDefault() {}});
  await tick();
  assert.deepEqual(requests[1].messages.map(m => m.content), ['Hello', 'Héllo', 'Again']);
  interrupted.controller.enqueue(encoder.encode('{"message":{"content":"partial"}}\n'));
  interrupted.controller.close();
  await failed;
  assert.match(items.at(-1).textContent, /partial\n\nRequest failed: Response ended/);
  assert.equal(send.disabled, false);

  const error = streamResponse();
  nextResponse = error.response;
  promptBox.value = 'Retry';
  const retry = handlers.submit({preventDefault() {}});
  await tick();
  assert.deepEqual(requests[2].messages.map(m => m.content), ['Hello', 'Héllo', 'Retry']);
  error.controller.enqueue(encoder.encode('{"error":"out of memory"}\n'));
  await retry;
  assert.match(items.at(-1).textContent, /Request failed: out of memory/);
  assert.equal(status.textContent, 'Connection error');
  console.log('Chat streaming tests passed');
})().catch(err => { console.error(err); process.exitCode = 1; });
