### Title
Unbounded buffer growth in entrypointLogForwarder.Write allows single-line attacker log stream to exhaust runner memory - (File: executors/kubernetes/container_entrypoint_forwarder.go)

### Summary
`entrypointLogForwarder.Write` accumulates bytes into `lf.buffer` for any data that does not contain a trailing `\n`, with no size cap on the buffer [1](#0-0) . Since the forwarder only forwards data to the size-limited `Sink` (`trace.Buffer`, capped at 4MB by `defaultBytesLimit`) once a full line is detected, a container entrypoint that emits large amounts of output without any newline character can grow `lf.buffer` in the Runner process's memory without any bound before the downstream trace log-size limit is ever consulted [2](#0-1) .

### Finding Description
`Write` scans the incoming slice `p` for `\n` bytes. For every complete line found, it calls `lf.writeLine`, which forwards the line to `lf.Sink.Write(p)` — this is where the trace log's `limitWriter` enforces the 4MB job log cap [3](#0-2) . Any trailing bytes after the last newline (or the entirety of `p` if no newline is present) are appended to `lf.buffer` via `lf.buffer = append(lf.buffer, rest...)` [4](#0-3) . This buffer is only ever flushed to `Sink` on the next newline or in `flush()`/`Close()` [5](#0-4) .

Because Kubernetes pod-attach/exec stdout streams are delivered to this `Write` method incrementally in chunks as the container process produces output, an attacker-controlled `image`/`entrypoint` (a job/pipeline author can set a custom image with a malicious `ENTRYPOINT` in `.gitlab-ci.yml`) that writes an arbitrarily large amount of stdout data without ever emitting a `\n` will cause `lf.buffer` to grow with every `Write` call, with no upper bound check anywhere in this file. The 4MB trace/log limit in `helpers/trace/buffer.go` is irrelevant here because it is only reached inside `Sink.Write`, which is never invoked while the current line remains incomplete. This is a genuine missing-check: there is no line-length or buffer-size cap in `entrypointLogForwarder`.

### Impact Explanation
The unbounded `lf.buffer` growth occurs in the GitLab Runner process (Kubernetes executor, which typically runs the manager/helper process), not inside the isolated container sandbox. Sustained growth (e.g., gigabytes of no-newline stdout) can drive the Runner manager process's memory usage up significantly, potentially triggering OOM conditions that affect the Runner host process — impacting other concurrently running jobs on the same Runner (cross-job disruption), consistent with the described scoped impact. The impact stays within log-stream processing (it does not grant sandbox escape or cross-project data access).

### Likelihood Explanation
This is straightforwardly reachable: any pipeline author who can specify a custom container `image`/`entrypoint` for a job (a normal capability in `.gitlab-ci.yml`) can trigger it deterministically by writing large chunks of stdout without newlines before entering a shell or exiting. No special privileges, race conditions, or unusual timing are required — it is fully attacker-controlled and repeatable.

### Recommendation
Impose a maximum size on `lf.buffer` in `entrypointLogForwarder.Write` (e.g., matching or below the trace log byte limit). When the accumulated unterminated-line buffer exceeds this limit, either flush/truncate it to `Sink` (letting the existing `limitWriter` truncation logic apply) or drop/cap further accumulation and emit a warning, rather than allowing indefinite `append` growth.

### Proof of Concept
Go unit/fuzz test in `executors/kubernetes/container_entrypoint_forwarder_test.go`:
```go
func TestWrite_UnboundedBufferGrowth(t *testing.T) {
    lf := &entrypointLogForwarder{Sink: &discardWriteCloser{}}
    chunk := bytes.Repeat([]byte("A"), 1<<20) // 1MB, no newline

    for i := 0; i < 4096; i++ { // simulate 4GB of no-newline output
        _, err := lf.Write(chunk)
        require.NoError(t, err)
    }

    // Assert: buffer size grows without bound / exceeds any reasonable cap
    assert.Greater(t, len(lf.buffer), 100*1024*1024,
        "lf.buffer grew unbounded with no size cap")
}
```
Expected assertion failure demonstrates `lf.buffer` has no cap; a fixed version should either cap `len(lf.buffer)` or force periodic flush/truncation to `Sink`, keeping memory bounded regardless of input length.

### Citations

**File:** executors/kubernetes/container_entrypoint_forwarder.go (L44-67)
```go
func (lf *entrypointLogForwarder) Write(p []byte) (int, error) {
	alreadyWritten := 0

	for i, b := range p {
		if b != '\n' {
			continue
		}

		err := lf.writeLine(append(lf.buffer, p[alreadyWritten:i+1]...))
		lf.buffer = nil
		if err != nil {
			return 0, err
		}

		alreadyWritten = i + 1
	}

	if alreadyWritten < len(p) {
		rest := p[alreadyWritten:]
		lf.buffer = append(lf.buffer, rest...)
	}

	return len(p), nil
}
```

**File:** executors/kubernetes/container_entrypoint_forwarder.go (L69-87)
```go
func (lf *entrypointLogForwarder) flush() error {
	rest := lf.buffer
	if len(rest) >= 1 {
		_, err := lf.Sink.Write(rest)
		return err
	}

	return nil
}

// Close flushes the remaining buffer into Sink and closes it.
func (lf *entrypointLogForwarder) Close() error {
	if err := lf.flush(); err != nil {
		defer lf.Sink.Close()
		return err
	}

	return lf.Sink.Close()
}
```

**File:** helpers/trace/buffer.go (L20-20)
```go
const defaultBytesLimit = 4 * 1024 * 1024 // 4MB
```

**File:** helpers/trace/buffer.go (L158-186)
```go
func (w *limitWriter) Write(p []byte) (int, error) {
	capacity := w.limit - w.written

	if capacity <= 0 {
		return 0, errLogLimitExceeded
	}

	if int64(len(p)) >= capacity {
		p = truncateSafeUTF8(p, capacity)
		n, err := w.w.Write(p)
		if err == nil {
			err = errLogLimitExceeded
		}
		if n < 0 {
			n = 0
		}
		w.written += int64(n)
		w.writeLimitExceededMessage()

		return n, err
	}

	n, err := w.w.Write(p)
	if n < 0 {
		n = 0
	}
	w.written += int64(n)
	return n, err
}
```
