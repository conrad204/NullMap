import assert from 'node:assert/strict';
import test from 'node:test';
import { readEventStream } from './stream.ts';

function stream(chunks) {
  return new ReadableStream({ start(controller) { for (const chunk of chunks) controller.enqueue(chunk); controller.close(); } });
}
const encoder = new TextEncoder();

test('SSE survives every UTF-8 byte and split CRLF boundary', async () => {
  const bytes = encoder.encode(': keepalive\r\nevent: progress\r\ndata: {"message":"μ, 肺, naïve"}\r\n\r\nevent: result\r\ndata: {"queryId":"done"}\r\n\r\n');
  const events = [];
  await readEventStream(stream([...bytes].map((byte) => new Uint8Array([byte]))), (event, data) => events.push({ event, data }));
  assert.deepEqual(events, [
    { event: 'progress', data: { message: 'μ, 肺, naïve' } },
    { event: 'result', data: { queryId: 'done' } },
  ]);
});

test('supports multiple events in one chunk, multiline data, and final unterminated line', async () => {
  const events = [];
  await readEventStream(stream([encoder.encode('event: progress\ndata: {\ndata: "stage": "searching"\ndata: }\n\nevent: result\ndata: {"count": 0}')]), (event, data) => events.push({ event, data }));
  assert.deepEqual(events, [{ event: 'progress', data: { stage: 'searching' } }, { event: 'result', data: { count: 0 } }]);
});

test('aborting a pending read cancels transport and rejects with AbortError', async () => {
  let canceled = false;
  const body = new ReadableStream({ cancel() { canceled = true; } });
  const controller = new AbortController();
  const pending = readEventStream(body, () => assert.fail('no event expected'), controller.signal);
  controller.abort();
  await assert.rejects(pending, { name: 'AbortError' });
  assert.equal(canceled, true);
  assert.equal(body.locked, false);
});

test('malformed data fails and releases the response reader', async () => {
  const body = stream([encoder.encode('event: result\ndata: invalid json\n\n')]);
  await assert.rejects(readEventStream(body, () => assert.fail('invalid data should not be emitted')), SyntaxError);
  assert.equal(body.locked, false);
});

test('callback errors propagate and release the response reader', async () => {
  const body = stream([encoder.encode('event: error\ndata: {"message":"Index unavailable"}\n\n')]);
  await assert.rejects(readEventStream(body, (event, data) => { if (event === 'error') throw new Error(data.message); }), /Index unavailable/);
  assert.equal(body.locked, false);
});
