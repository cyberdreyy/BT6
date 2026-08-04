### Title
Runner's own logrus output bypasses secret masking chain in `internal.Tee.log` - ([File: common/buildlogger/internal/tee.go])

### Summary
`internal.Tee.log` sanitizes only the copy of a log line sent to the build trace (`t.logFn`, which is `Logger.SendRawLog` routed through the masked `l.w` writer chain), but forwards the raw, unmasked `args` directly to `t.entry.Logln(level, args...)`, the runner's own logrus entry. Any secret value (job variables registered via `Options.MaskPhrases`, tokens matched by `tokensanitizer`, or sensitive URL params handled by `urlsanitizer`) that ends up as an argument to `Logger.Errorln`/`Warningln`/`SoftErrorln`/`Println`/`Infoln` will appear unmasked in the runner's structured logs even though it is properly masked in the job trace.

### Finding Description
`Logger` embeds `internal.Tee` [1](#0-0) . `Logger.wrap` builds the masking chain (`masker` → `urlsanitizer` → `tokensanitizer` → `timestamper`) that only wraps `l.w`, which backs `l.base`/the build trace [2](#0-1) . `Logger.SendRawLog` writes straight into `l.w` (the masked writer) [3](#0-2) .

`internal.Tee.log` is the shared implementation behind `Println`, `Infoln`, `Warningln`, `SoftErrorln`, and `Errorln`. It builds `logLine` from `args`, sends the (soon-to-be-masked, since `t.logFn` funnels through `l.w`) `logPrefix+logLine` string to `t.logFn`, but then — completely independently, and using the original un-sanitized `args` — calls `t.entry.Logln(level, args...)` directly on the runner's logrus entry [4](#0-3) . There is no masker/urlsanitizer/tokensanitizer application anywhere in this second branch; it is a separate codepath entirely disconnected from the `l.w` wrap chain that provides masking for the trace.

Numerous production call sites pass build/job error content — which can include user/pipeline-controlled variable values or command output — into `Errorln`/`Warningln`, e.g. in `common/build.go`, `executors/docker/docker.go`, `executors/kubernetes/kubernetes.go`, and `executors/custom/custom.go`. Since `MaskPhrases` (which includes CI/CD masked variables) are only ever applied via the `l.w` chain, any of these callers passing a string containing a masked secret value will leak that value verbatim into `t.entry`'s output — the runner's own logrus/host log — while the job trace shown to the user correctly shows it masked.

### Impact Explanation
Runner host logs (or any log-aggregation/support surface consuming the runner's own logrus output) can receive protected/masked secret values that are supposed to be redacted, even though the job trace itself correctly hides them. This is a concrete, cross-tenant-relevant secret exposure: a user with only job/pipeline authorship privileges can cause a secret (their own masked variable or one incidentally routed into an error message) to be written unmasked to a surface (runner host logs) that should never see it — matching the "masked values must not leak across trace vs. runner logs" invariant.

### Likelihood Explanation
This is trivially reachable: any error path in Runner code that formats a build error/output string containing a masked value and passes it to `Logln`/`Errorln`/`Warningln` (many exist across executors and `common/build.go`) will trigger the leak. No special executor privilege or admin misconfiguration is required — it's an architectural gap between the trace masking chain and the runner-log branch inside `Tee.log`, and is deterministic/repeatable for any qualifying error path.

### Recommendation
Apply the same sanitization chain (masker, urlsanitizer, tokensanitizer) to the string/args passed to `t.entry.Logln` in `internal.Tee.log`, e.g., by running the already-computed `logLine` (or a masked variant of `args`) through the same sanitizers used for `t.logFn`, rather than passing raw `args` to logrus. Concretely, mask `args`/`logLine` in `internal.Tee.log` before calling `t.entry.Logln(level, ...)`, or refactor so the masked line produced for `t.logFn` is also the one fed to `t.entry`.

### Proof of Concept
Go unit test extending `build_logger_test.go`'s `runOnHijackedLogrusOutput` fixture:
1. Create a `Logger` via `New` with `Options.MaskPhrases = []string{"SECRET_VALUE"}` and a fake `Trace` plus a hijacked logrus `entry`/hook capturing log records.
2. Call `logger.Errorln("build failed: token=SECRET_VALUE")`.
3. Assert `jt.Read()` (the build trace) contains the masked form (e.g. `[MASKED]` or `x`-replaced) — expected to pass today.
4. Assert the hijacked logrus hook's captured entries do **not** contain the literal string `"SECRET_VALUE"` — expected to **fail** today, proving `t.entry.Logln(level, args...)` in `common/buildlogger/internal/tee.go` leaks the raw value into the runner's own log output.

### Citations

**File:** common/buildlogger/build_logger.go (L39-53)
```go
type Logger struct {
	internal.Tee

	base   io.WriteCloser
	closed bool

	// mu protects w, as Tee's Println, Debugln etc. funcs can be called
	// throughout the runner from different go routines.
	mu *sync.Mutex
	w  io.WriteCloser

	maskPhrases       [][]byte
	maskTokenPrefixes [][]byte
	timestamping      bool
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

**File:** common/buildlogger/build_logger.go (L238-246)
```go
func (l *Logger) SendRawLog(args ...any) {
	if l.w == nil {
		return
	}

	l.mu.Lock()
	_, _ = fmt.Fprint(l.w, args...)
	l.mu.Unlock()
}
```

**File:** common/buildlogger/internal/tee.go (L49-74)
```go
func (t *Tee) log(level logrus.Level, logPrefix string, args ...interface{}) {
	if t.entry == nil {
		return
	}

	// log lines have spaces between each argument, followed by an ANSI Reset and *then* a new-line.
	//
	// To achieve this, we use fmt.Sprintln and remove the newline, add the ANSI Reset and then
	// append the newline again. The reason we don't use fmt.Sprint is that there's a greater
	// difference between that and fmt.Sprintln than just the newline character being added
	// (fmt.Sprintln consistently adds a space between arguments).
	logLine := fmt.Sprintln(args...)
	logLine = logLine[:len(logLine)-1]
	logLine += helpers.ANSI_RESET + "\n"

	if t.logFn != nil {
		t.logFn(logPrefix + logLine)
	}

	// don't tee to logrus entry (runner log) when disabled or no args
	if t.noLog || len(args) == 0 {
		return
	}

	t.entry.Logln(level, args...)
}
```
