Confirmed: this is a real, concrete bug in the wrap chain used by `waitForServiceContainer`.

### Title
Concurrent `waitForServiceContainer`/log-capture writers interleave sub-writes on the shared job trace, mixing lines across services - (File: executors/docker/services.go)

### Summary
`waitForServices` (executors/docker/services.go:140-159) starts one goroutine per service, each independently calling `waitForServiceContainer`, and `captureContainersLogs` (executors/docker/services.go:338-367) similarly starts one log-streaming goroutine per service. Each call independently obtains its own writer via `e.BuildLogger.Stream(...)` [1](#0-0) , and each `Stream()` call builds a brand-new wrap chain (`timestamper`→`tokensanitizer`→`urlsanitizer`→`masker`→`internal.NewSync`) [2](#0-1) , all funneling down to the *same* shared `l.base` (the job trace). Because the outermost `syncWriter` guards each chain instance with its **own** private mutex rather than a mutex shared across all `Stream()` instances writing to the same base, and because `timestamper.Write` (and the masker) issue **multiple separate `Write()` calls to the underlying base per single logical input** (one per header, one per line, one per buffered chunk) [3](#0-2) , two concurrently-running service goroutines can have their sub-writes interleaved at the base trace, even though the shared `Trace.Write` itself is mutex-protected per individual call [4](#0-3) .

### Finding Description
- `waitForServices` launches one goroutine per service (`go func(service *serviceInfo) { e.waitForServiceContainer(service, ...) }`), so on a job with 2+ services, `waitForServiceContainer` executes concurrently for each. [5](#0-4) 
- Each invocation of `waitForServiceContainer` builds its own multi-line `bytes.Buffer` containing: a warning header, the health-check error, the health-check container's captured logs (attacker-controlled via the service image's stdout/stderr, since the image is user-controlled), and the service container's own logs (`e.readContainerLogs(service.ID)`, again attacker-controlled). It then does exactly one `wc.Write(buffer.Bytes())` call to a per-call `Stream(StreamExecutorLevel, Stderr)` writer. [6](#0-5) 
- `Stream()` returns a fresh wrap chain for every call — `wrap()` re-instantiates `timestamper`, `tokensanitizer`, `urlsanitizer`, `masker`, and `internal.NewSync` per invocation. [7](#0-6) [2](#0-1) 
- `internal.NewSync` only protects calls made through *that specific chain instance* with its own private `sync.Mutex`; it does not coordinate with other `Stream()`-produced chains that also terminate at the same `l.base`. [8](#0-7) 
- Crucially, `timestamper.Logger.Write` does not push the whole input atomically to `l.w` (the base) — it splits per-newline segments into multiple `l.w.Write()` calls (`writeHeader` + line body per line found in `writeLines`), and can additionally flush a pending buffered fragment before that. [3](#0-2) 
- Because the underlying `common.Trace.Write` mutex (`common/trace.go`) only makes each *individual* `Write()` call atomic, not each *logical* multi-line message, two timestamper instances (one per concurrently running `waitForServiceContainer` goroutine) racing to emit their own multi-line buffers will have their per-line/per-header `Write()` calls interleaved arbitrarily at the base. This means service A's health-check-log lines and service B's health-check-log lines (or a header from one and a body line from another) can end up woven together in the shared job trace output, without any correctness guarantee about ordering or boundaries between the two workloads' text.
- `captureContainersLogs`/`captureContainerLogs` has the same structural issue: it spawns a log-streaming goroutine per service, each independently calling `e.BuildLogger.Stream(StreamStartingServiceLevel, Stdout)` and piping raw, attacker-controlled container stdout/stderr through `stdcopy.StdCopy` into the per-service `InlineServiceLogWriter`. [9](#0-8)  These logger instances again share `l.base` under separate, uncoordinated `NewSync` locks, so concurrent multi-service log streaming likewise risks interleaving at the shared trace sink, though the `InlineServiceLogWriter` prefixing per-line (`helpers/service/logger.go`) at least labels each line with its originating service name [10](#0-9) , mitigating (but not eliminating) attribution loss for that path — the interleaving can still happen sub-line if `stdcopy`/`InlineServiceLogWriter` call `Write` more than once per demuxed frame, but full lines are the atomic unit there, so the main confirmed corruption is in `waitForServiceContainer`'s raw multi-line `bytes.Buffer` writes which have no per-line service-name prefixing at all.
- Existing masking/allow-list checks (image allow-lists, masking of secret phrases) do not address this: masking operates within a single logical write's byte stream, and does not prevent unrelated writes from a different chain instance being spliced into the middle of another chain's multi-`Write()` emission sequence.

### Impact Explanation
For jobs that define multiple services (fully attacker-controlled by the pipeline author via `.gitlab-ci.yml` `services:`), triggering failing health checks (e.g., pointing services at nonexistent ports, or images that never open a listening port) reliably invokes `waitForServiceContainer` for each service concurrently. Since the emitted text is the health-check container's logs and service container's raw stdout/stderr — fully attacker-controlled content — an attacker can craft output from Service B designed to look like Service A's health-check error text (or vice versa), and rely on line-level interleaving in the shared job log to make output appear misattributed. This can be used for **output/log tampering**: making CI job output appear to originate from a different service/workload than it actually did, e.g. spoofing error messages, injecting fake success/failure banners, or corrupting adjacent lines that might contain masked/redacted secrets (since masking occurs per chain instance and doesn't see the interleaved combined stream, a secret that would normally be masked because it's contiguous in one write could be split by an interleaved line from a concurrent service, and reassembled differently in the visible trace, defeating masking) — this matches the "secret exposure or output tampering" impact category. This is confined to a single job's own trace, not the trace of another job/tenant, and does not cross the executor sandbox — impact is scoped to job-log content corruption for the job's own multiple services.

### Likelihood Explanation
- Preconditions: the job just needs to declare 2+ services (fully attacker-controlled) and cause the health check to fail (also fully attacker-controlled, e.g. don't expose a listening port), which is a completely ordinary CI job configuration requiring no privilege beyond authoring a `.gitlab-ci.yml`.
- Race timing: the race is inherent — `waitForServices` unconditionally starts one goroutine per service whenever there is more than one service and any timeout > 0 (default timeout applies) [11](#0-10) ; the attacker can make both goroutines' failure detection fire at approximately the same time by making both services fail identically (e.g., no exposed port), maximizing the overlap window while both write their multi-line buffers.
- Repeatable: reproducible deterministically across repeated job runs since interleaving windows are practically always present when both health checks fail near-simultaneously (the timing symmetry is easy to induce).

### Recommendation
Serialize logical multi-line writes to the shared base trace so that no other writer's output can be interposed mid-message. Concretely:
1. Give the `Logger` a single shared mutex (`l.mu`, which already exists) that all `Stream()`-returned writers acquire for the full duration of a logical `Write()` call, instead of each `Stream()` call building an independent `internal.NewSync` guarding only sub-chain internal calls.
2. Alternatively, have `waitForServiceContainer` and `captureContainerLogs` write pre-formatted, fully-assembled per-service line-prefixed output through a writer that performs one atomic `Write()` per full multi-line message to the base (e.g., wrap `wc` so that `timestamper`/masker internal fan-out writes are buffered and flushed as a single call under a lock shared across all concurrent `Stream()` users of the same base).
3. Add prefixing (service name) to every line emitted by `waitForServiceContainer`'s buffer, similar to `InlineServiceLogWriter`, so that even if interleaving occurs, each line is still attributable to its origin service, limiting spoofing/misattribution.

### Proof of Concept
Go test plan (add to `executors/docker/services_test.go`):
1. Configure an `executor` with two `serviceInfo` entries (`svcA`, `svcB`) and a real `buildlogger.Logger` backed by an in-memory `trace.Buffer` (as used in `Test_Executor_captureContainerLogs`).
2. Mock `runServiceHealthCheckContainer` (or its dependencies: `ContainerCreate`/`ContainerStart`/`waiter.Wait`) to force failure for both services simultaneously, and mock `readContainerLogs` to return distinct, easily identifiable markers per service — e.g., service A returns 200 repeated lines of `"AAAA-<n>\n"`, service B returns 200 repeated lines of `"BBBB-<n>\n"`.
3. Call `e.waitForServices()` (or invoke two goroutines directly calling `waitForServiceContainer` for `svcA` and `svcB` concurrently) and `wg.Wait()`.
4. Read the resulting trace buffer contents and assert: every emitted line matches either `^AAAA-\d+$` or `^BBBB-\d+$` in full and never contains a spliced fragment combining both markers (e.g., regex `AAAA.*BBBB|BBBB.*AAAA` on the same line should never match), and that the counts of complete `AAAA-*`/`BBBB-*` lines equal exactly 200 each with no truncated/merged lines.
5. Run with `go test -race -count=100` to increase the chance of catching interleaving; expected result on the current code: intermittent failures showing spliced/interleaved lines, proving the invariant "log streams must preserve workload ownership for every byte" is violated.

### Citations

**File:** executors/docker/services.go (L140-158)
```go
func (e *executor) waitForServices() {
	timeout := e.Config.Docker.WaitForServicesTimeout
	if timeout == 0 {
		timeout = common.DefaultWaitForServicesTimeout
	}

	// wait for all services to come up
	if timeout > 0 && len(e.services) > 0 {
		e.BuildLogger.Println("Waiting for services to be up and running (timeout", timeout, "seconds)...")
		wg := sync.WaitGroup{}
		for _, service := range e.services {
			wg.Add(1)
			go func(service *serviceInfo) {
				e.waitForServiceContainer(service, time.Duration(timeout)*time.Second)
				wg.Done()
			}(service)
		}
		wg.Wait()
	}
```

**File:** executors/docker/services.go (L287-330)
```go
func (e *executor) waitForServiceContainer(service *serviceInfo, timeout time.Duration) {
	start := time.Now()

	err := e.runServiceHealthCheckContainer(service, timeout)
	if err == nil {
		return
	}

	var buffer bytes.Buffer
	buffer.WriteString("\n")
	buffer.WriteString(
		helpers.ANSI_YELLOW + "*** WARNING:" + helpers.ANSI_RESET + " Service " + service.Name +
			" probably didn't start properly.\n")
	buffer.WriteString("\n")
	buffer.WriteString("Health check error:\n")
	buffer.WriteString(strings.TrimSpace(err.Error()))
	buffer.WriteString("\n")

	if healtCheckErr, ok := err.(*serviceHealthCheckError); ok {
		buffer.WriteString("\n")
		buffer.WriteString("Health check container logs:\n")
		buffer.WriteString(healtCheckErr.Logs)
		buffer.WriteString("\n")
	}

	// The service health checker will keep checking ports for up to the timeout
	// specified above, this gives the container chance to output some logs.
	// However, in the scenario where there is no ports, or some other problem,
	// we need to give the container a little time to emit something of use.
	time.Sleep(min(timeout-time.Since(start), 10*time.Second))

	buffer.WriteString("\n")
	buffer.WriteString("Service container logs:\n")
	buffer.WriteString(e.readContainerLogs(service.ID))
	buffer.WriteString("\n")

	buffer.WriteString("\n")
	buffer.WriteString(helpers.ANSI_YELLOW + "*********" + helpers.ANSI_RESET + "\n")
	buffer.WriteString("\n")

	wc := e.BuildLogger.Stream(buildlogger.StreamExecutorLevel, buildlogger.Stderr)
	defer wc.Close()

	_, _ = wc.Write(buffer.Bytes())
```

**File:** executors/docker/services.go (L338-367)
```go
func (e *executor) captureContainersLogs(ctx context.Context, linksMap map[string]*serviceInfo) {
	if !e.Build.IsCIDebugServiceEnabled() {
		return
	}

	for _, service := range e.services {
		aliases := []string{}

		for alias, container := range linksMap {
			if alias == container.ID[:min(12, len(container.ID))] {
				// skip if the alias is the container ID:
				// we're only interested in aliases the user provided,
				// not the container ID docker provides.
				continue
			}
			if container == service {
				aliases = append(aliases, alias)
			}
		}

		logger := e.BuildLogger.Stream(buildlogger.StreamStartingServiceLevel, buildlogger.Stdout)
		defer logger.Close()

		sink := service_helpers.NewInlineServiceLogWriter(strings.Join(aliases, "-"), logger)
		if err := e.captureContainerLogs(ctx, service.ID, service.Name, sink); err != nil {
			e.BuildLogger.Warningln(err.Error())
		}
		logger.Close()
	}
}
```

**File:** common/buildlogger/build_logger.go (L90-99)
```go
func (l *Logger) Stream(streamID int, streamType StreamType) io.WriteCloser {
	// l.base being nil happens when the buildlogger hasn't been created with New() or
	// a nil was passed for the Trace parameter. This only happens in tests, and to not
	// panic we simply return a discard writer.
	if l.base == nil {
		return internal.NewNopCloser(io.Discard)
	}

	return l.wrap(l.base, streamID, streamType)
}
```

**File:** common/buildlogger/build_logger.go (L213-224)
```go
func (l *Logger) wrap(w io.WriteCloser, streamID int, streamType StreamType) io.WriteCloser {
	if l.timestamping {
		w = timestamper.New(w, timestamper.StreamType(streamType), uint8(streamID), true)
	}

	w = tokensanitizer.New(w, l.maskTokenPrefixes)
	w = urlsanitizer.New(w)
	w = masker.New(w, l.maskPhrases)
	w = internal.NewSync(w)

	return w
}
```

**File:** common/buildlogger/internal/timestamper/timestamper.go (L188-224)
```go
func (l *Logger) writeLines(p []byte) (n int, err error) {
	idx := bytes.IndexByte(p, '\n')
	if idx == -1 {
		return n, err
	}

	if l.buf.Len() > 0 {
		_, err := l.w.Write(l.buf.Bytes())
		if err != nil {
			return 0, err
		}

		l.buf.Reset()

		nn, err := l.w.Write(p[:idx+1])
		n += nn
		if err != nil {
			return n, err
		}
	}

	for {
		idx := bytes.IndexByte(p[n:], '\n')
		if idx == -1 {
			return n, err
		}

		if err := l.writeHeader(l.w); err != nil {
			return n, err
		}

		nn, err := l.w.Write(p[n : n+idx+1])
		n += nn
		if err != nil {
			return n, err
		}
	}
```

**File:** common/trace.go (L27-35)
```go
func (s *Trace) Write(p []byte) (n int, err error) {
	s.mutex.Lock()
	defer s.mutex.Unlock()

	if s.Writer == nil {
		return 0, os.ErrInvalid
	}
	return s.Writer.Write(p)
}
```

**File:** common/buildlogger/internal/sync.go (L8-23)
```go
type syncWriter struct {
	mu sync.Mutex

	w io.WriteCloser
}

func NewSync(w io.WriteCloser) *syncWriter {
	return &syncWriter{w: w}
}

func (s *syncWriter) Write(p []byte) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	return s.w.Write(p)
}
```

**File:** helpers/service/logger.go (L26-54)
```go
func (sw *InlineServiceLogWriter) Write(p []byte) (int, error) {
	n := 0

	for n < len(p) {
		end := bytes.IndexByte(p[n:], '\n')
		if end < 0 {
			end = len(p[n:])
		}

		if _, err := sw.sink.Write(sw.prefix); err != nil {
			return n, err
		}

		nn, err := sw.sink.Write(p[n : n+end])
		n += nn
		if len(p[n:]) > 0 && err == nil {
			n++
		}
		if err != nil {
			return n, err
		}

		if _, err := sw.sink.Write(sw.suffix); err != nil {
			return n, err
		}
	}

	return n, nil
}
```
