### Title
Direct `fmt.Fprintln(trace, err.Error())` in `RunCommand.traceOutcome` bypasses the `buildlogger.Logger` masking chain, leaking masked CI variable values embedded in executor/system-failure error messages - (File: commands/multi.go)

### Summary
`RunCommand.traceOutcome`/`RunCommand.processBuildOnRunner` write the error text of a failed job directly onto the raw `common.JobTrace` object handed to `build.Run`, not through the `buildlogger.Logger` instance that `build.Run` builds internally for masking. Since phrase-masking of literal secret values (`maskPhrases`) is only implemented inside `buildlogger.Logger.wrap()`'s `masker.New` stage, and that wrapper is private to the `Logger` created around the trace, any error string containing a masked variable's literal value that reaches `traceOutcome` is written to the job log unmasked.

### Finding Description
`buildlogger.New()` builds an internal, private write chain around the `Trace`/`common.JobTrace` it's given: `l.base = internal.NewNopCloser(log)` and `l.w = l.wrap(l.base, ...)`, where `wrap()` composes `timestamper -> tokensanitizer -> urlsanitizer -> masker` [1](#0-0) . Crucially, masking of literal masked-variable phrases (`l.maskPhrases`, populated from `Options.MaskPhrases`) only happens for writes that go through `l.w`/`l.Tee` — i.e., calls like `Logger.Println`, `Logger.SendRawLog`, or writers obtained via `Logger.Stream()` [2](#0-1) . The raw `common.JobTrace`/`Trace` object passed into `buildlogger.New` (the `log` parameter) has no masking of its own for arbitrary phrase content; masking is added only by the wrapper that `buildlogger.New` constructs, and that wrapper is internal to the `Logger` value used inside `build.Run`.

`RunCommand.traceOutcome` and `RunCommand.processBuildOnRunner` in `commands/multi.go` operate on the same `common.JobTrace` reference that was handed to `build.Run`, but they are outside of and separate from the `Logger` instance `build.Run` creates for masking normal job output. When `build.Run` fails (e.g., returns a `RunnerSystemFailure`), `traceOutcome` writes `err.Error()` straight to that raw trace object (`fmt.Fprintln(trace, err.Error())`) before calling `trace.Fail(err, reason)`. This path does not pass through `masker.New`, `tokensanitizer.New`, or `urlsanitizer.New`, so any masked value embedded in the error text is written to the trace verbatim.

An unprivileged pipeline author can trigger this by referencing a masked CI/CD variable in a field that gets echoed back into a Go `error` on failure — the canonical case being an `image:`/service definition built from a masked variable that fails validation or pull inside an executor (`Prepare()`), producing an error such as `fmt.Errorf("invalid reference %q: %w", image, err)` where `image` contains the expanded masked value. That error is classified as a `RunnerSystemFailure`, propagates up through `build.Run`, and reaches `traceOutcome`, which writes it unmasked via `fmt.Fprintln`.

Existing protections (the `masker`/`tokensanitizer`/`urlsanitizer` chain) only guard writes made through the `buildlogger.Logger` wrapper; they provide no protection for code paths, like `traceOutcome`, that write to the underlying `common.JobTrace` directly.

### Impact Explanation
Any masked CI/CD variable value that an attacker (pipeline author) can get embedded into an executor/system-failure `error.Error()` string is exposed in plaintext in the job log/trace, which is visible to anyone who can view job output (other maintainers, protected-branch viewers, artifacts/log retention consumers) — a cross-viewer secret disclosure that the masking invariant is specifically meant to prevent.

### Likelihood Explanation
Feasible and repeatable: a pipeline author only needs to (1) define/use a masked variable, (2) reference it somewhere that becomes part of a failure message the runner turns into a `RunnerSystemFailure` (e.g., a malformed image name derived from the variable), and (3) let the job fail. No special runner privileges are required — only standard CI job authoring capability.

### Recommendation
Route all writes made in `traceOutcome`/`processBuildOnRunner` (and any other direct writers to `common.JobTrace`) through the same masking pipeline used by `buildlogger.Logger`, e.g., by masking `err.Error()` with the job's `maskPhrases`/`tokensanitizer`/`urlsanitizer` before calling `fmt.Fprintln(trace, ...)`, or by exposing a masked-write helper from `buildlogger` for out-of-band error reporting so no code path can write raw error text to `common.JobTrace` unmasked.

### Proof of Concept
```go
// commands/multi_test.go (illustrative)
func TestTraceOutcomeDoesNotLeakMaskedValue(t *testing.T) {
    secret := "s3cr3t-value"
    fakeTrace := &fakeJobTrace{buf: &bytes.Buffer{}}
    err := fmt.Errorf("invalid reference \"%s\": system failure", secret)

    mr := &RunCommand{}
    mr.traceOutcome(fakeTrace, err) // calls fmt.Fprintln(trace, err.Error()) then trace.Fail(...)

    assert.NotContains(t, fakeTrace.buf.String(), secret,
        "masked value leaked into JobTrace via traceOutcome, bypassing buildlogger masking chain")
}
```
Expected (current) result: the assertion fails — `fakeTrace.buf` contains `secret` in plaintext, confirming the masking bypass.

### Citations

**File:** common/buildlogger/build_logger.go (L68-88)
```go
func New(log Trace, entry *logrus.Entry, opts Options) Logger {
	l := Logger{mu: new(sync.Mutex)}

	l.maskPhrases = internal.Unique(opts.MaskPhrases)
	l.maskTokenPrefixes = internal.Unique(
		append(opts.MaskTokenPrefixes, tokensanitizer.DefaultTokenPrefixes(opts.MaskAllDefaultTokens)...),
	)
	l.timestamping = opts.Timestamping

	if log != nil {
		l.base = internal.NewNopCloser(log)
		l.w = l.wrap(l.base, StreamExecutorLevel, Stdout)
	}

	l.Tee = internal.NewTee(l.SendRawLog, entry, log != nil && log.IsStdout())
	if opts.TeeOnly {
		l.Tee = l.Tee.WithoutLog()
	}

	return l
}
```

**File:** common/buildlogger/build_logger.go (L203-224)
```go
// wrap wraps the underlying writer with "filters". Order here somewhat
// matters, and the order they're instantiated in is the reverse order in which
// writes are processed, e.g. last added filter is the first to process data.
//
// order:
// - sync writer to ensure that multiple writes cannot happen concurrently
// - mask phrases (masker.New)
// - mask sensitive URL parameters (urlsanitizer.New)
// - mask secrets with a prefixed token (tokentanitizer.New)
// - split log lines and add timestamps (timestamper.New)
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
