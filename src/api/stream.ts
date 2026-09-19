/** Parse POST response SSE, preserving UTF-8 across arbitrary transport chunks. */
export async function readEventStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: string, data: unknown) => void,
  signal?: AbortSignal,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let event = "message";
  let data: string[] = [];

  function line(value: string) {
    if (!value) {
      if (data.length) onEvent(event, JSON.parse(data.join("\n")) as unknown);
      event = "message";
      data = [];
      return;
    }
    if (value.startsWith(":")) return;
    const colon = value.indexOf(":");
    const field = colon < 0 ? value : value.slice(0, colon);
    const raw = colon < 0 ? "" : value.slice(colon + 1);
    const payload = raw.startsWith(" ") ? raw.slice(1) : raw;
    if (field === "event") event = payload;
    if (field === "data") data.push(payload);
  }
  function drain(final = false) {
    let start = 0;
    for (let i = 0; i < buffer.length; i += 1) {
      if (buffer[i] !== "\n" && buffer[i] !== "\r") continue;
      // Wait for the next chunk to distinguish CR from CRLF.
      if (buffer[i] === "\r" && i === buffer.length - 1 && !final) break;
      line(buffer.slice(start, i));
      if (buffer[i] === "\r" && buffer[i + 1] === "\n") i += 1;
      start = i + 1;
    }
    buffer = buffer.slice(start);
    if (final && buffer) {
      line(buffer);
      buffer = "";
    }
  }
  const abort = () => { void reader.cancel().catch(() => undefined); };
  signal?.addEventListener("abort", abort, { once: true });
  try {
    signal?.throwIfAborted();
    while (true) {
      const chunk = await reader.read();
      signal?.throwIfAborted();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      drain();
    }
    buffer += decoder.decode();
    drain(true);
    line("");
  } finally {
    signal?.removeEventListener("abort", abort);
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
