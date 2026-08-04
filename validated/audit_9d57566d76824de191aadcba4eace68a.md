### Title
Masking bypass via step-runner "pre-stamped" passthrough detection - (File: common/buildlogger/build_logger.go)

### Summary
`Logger.StepRunnerStream` decides, on the very first `Write`, whether an entire log stream will be routed through a raw `passthrough` writer (bypassing `masker`/`tokensanitizer`/`urlsanitizer`) based solely on a 28-byte header shape heuristic in `isPreStamped`. Because the decision is made once via `sync.Once` and cached in `s.chosen` for the lifetime of the stream, any writer able to produce first-write bytes matching that shape — genuinely or by mimicking it — causes all subsequent bytes on that stream, including secrets, to reach `l.base` completely unmasked.

### Finding Description
`StepRunnerStream` constructs a `stepRunnerStream` whose `Write` calls `s.once.Do(s.pickMode(p))` only on the first invocation [1](#0-0) . `pickMode` checks `isPreStamped(p) && s.timestamping`, and if true, permanently sets `s.chosen = s.passthrough`, which is `internal.NewSync(l.base)` — i.e., raw writes straight to the trace with no masking chain at all [2](#0-1) [3](#0-2) .

`isPreStamped` only validates that bytes at fixed offsets (4,7,10,13,16,19,26,27) equal `-`, `-`, `T`, `:`, `:`, `.`, `Z`, ` ` — a structural/shape check, not a cryptographic or content-integrity check tying the header to a legitimate source [4](#0-3) . This exact header shape is also what the Runner's own `timestamper.Logger.writeHeader` emits for every line it timestamps [5](#0-4) , confirming the shape is a plain, unauthenticated, well-known, reproducible pattern (`YYYY-MM-DDTHH:MM:SS.UUUUUUZ<space>`), not a random/secret token.

`StepRunnerStream` is fed with bytes coming directly from the step-runner subprocess's stdout/stderr, which in turn relays output produced by user-authored step scripts (executed via `include:`/custom steps). Since the header-shape check is purely positional, an attacker-influenced first write — whether it is step-runner's own genuine timestamp header wrapping attacker-echoed secret content, or a script deliberately crafting 28 bytes matching the pattern — flips the stream's mode to `passthrough` once and for all. Because `s.chosen` is fixed for the entire stream's lifetime, **every subsequent `Write` call on that same stream, regardless of its own content, is also routed to `passthrough`**, permanently skipping `masker.New`, `urlsanitizer.New`, and `tokensanitizer.New` (the exact chain applied in the normal `wrap` path) [6](#0-5) . Any job-defined masked variable, token, or secret that ends up in that stream's later writes is written to the trace verbatim.

No other check in this file authenticates that the "pre-stamped" bytes actually originated from a trusted, non-attacker-influenced source before granting passthrough status.

### Impact Explanation
Once a stream is classified pre-stamped with timestamping enabled, all subsequent output on that step-runner stream bypasses the entire masking/sanitization chain permanently, allowing masked CI/CD variables, tokens, or other protected secrets emitted anywhere later in that same stream to be exposed verbatim in the job trace, visible to any trace viewer with pipeline access.

### Likelihood Explanation
Requires the timestamping feature flag enabled and a job author (attacker) who can influence what step-runner writes to stdout/stderr for a custom/catalog step — both are within the stated unprivileged threat model (ordinary CI pipeline author). Producing 8 fixed separator bytes at known offsets is trivial and fully deterministic; no randomness or secret is needed to satisfy `isPreStamped`. The bypass is "sticky" for the whole stream once triggered on the first write, making it highly repeatable within a single job run.

### Recommendation
Do not let a purely positional byte-shape check on caller/subprocess-supplied data grant a permanent, per-stream exemption from masking. Options:
- Re-run the masking/sanitization chain on the header-stripped body of passthrough writes as well (apply `masker`/`urlsanitizer`/`tokensanitizer` even in the pre-stamped path), instead of a full unmediated writer to `l.base`.
- Re-evaluate `isPreStamped` per `Write` call rather than caching the decision once for the stream's lifetime, so a stream cannot "poison" all future writes based on a single crafted/matching prefix.
- Establish an authenticated channel/framing between step-runner and Runner for signaling "already stamped" (e.g., an explicit out-of-band flag on the stream/pipe) rather than sniffing content bytes.

### Proof of Concept
```go
func TestStepRunnerStream_PassthroughLeaksSecretAfterFakeHeader(t *testing.T) {
    var traceBuf bytes.Buffer
    trace := &fakeTrace{w: &traceBuf} // implements Trace, IsStdout() = true

    l := New(trace, logrus.NewEntry(logrus.New()), Options{
        MaskPhrases:  []string{"SUPER_SECRET_TOKEN"},
        Timestamping: true,
    })

    w := l.StepRunnerStream(1, Stdout)

    // Craft 28 bytes matching isPreStamped's positional checks exactly,
    // then embed a secret string in the same buffer.
    header := []byte("2024-01-01T00:00:00.000000Z ")
    payload := append(header, []byte("leaking SUPER_SECRET_TOKEN\n")...)

    _, err := w.Write(payload)
    require.NoError(t, err)
    _ = w.Close()

    // Assert the masker never redacted the secret because the stream
    // was routed via passthrough.
    require.Contains(t, traceBuf.String(), "SUPER_SECRET_TOKEN")

    // Second write with unrelated content should ALSO bypass masking,
    // proving the mode is sticky for the stream's lifetime.
    w2 := l.StepRunnerStream(2, Stdout)
    _, _ = w2.Write(header) // establishes passthrough mode
    _, _ = w2.Write([]byte("second SUPER_SECRET_TOKEN leak\n"))
    _ = w2.Close()
    require.Contains(t, traceBuf.String(), "second SUPER_SECRET_TOKEN leak")
}
```
Expected assertions: the trace output contains the unmasked secret string in both writes, demonstrating that `passthrough` mode permanently bypasses `masker`/`urlsanitizer`/`tokensanitizer` for the entire stream once the first write matches `isPreStamped`.

### Citations

**File:** common/buildlogger/build_logger.go (L118-130)
```go
	return &stepRunnerStream{
		timestamping: l.timestamping,
		passthrough:  internal.NewSync(l.base),
		buildStripped: func() io.WriteCloser {
			return newInnerStreamStripper(
				l.wrap(l.base, streamID, Stdout),
				l.wrap(l.base, streamID, Stderr),
			)
		},
		buildWrapped: func() io.WriteCloser {
			return l.wrap(l.base, streamID, streamType)
		},
	}
```

**File:** common/buildlogger/build_logger.go (L142-153)
```go
// isPreStamped reports whether p starts with a runner timestamper header
// (YYYY-MM-DDTHH:MM:SS.UUUUUUZ<space>). Validating the full shape, not
// just byte 26, hardens the detection against producers other than
// step-runner that might happen to put 'Z' at byte 26.
func isPreStamped(p []byte) bool {
	if len(p) < 28 {
		return false
	}
	return p[4] == '-' && p[7] == '-' && p[10] == 'T' &&
		p[13] == ':' && p[16] == ':' && p[19] == '.' &&
		p[26] == 'Z' && p[27] == ' '
}
```

**File:** common/buildlogger/build_logger.go (L155-158)
```go
func (s *stepRunnerStream) Write(p []byte) (int, error) {
	s.once.Do(s.pickMode(p))
	return s.chosen.Write(p)
}
```

**File:** common/buildlogger/build_logger.go (L167-178)
```go
func (s *stepRunnerStream) pickMode(p []byte) func() {
	return func() {
		switch {
		case isPreStamped(p) && s.timestamping:
			s.chosen = s.passthrough
		case isPreStamped(p):
			s.chosen = s.buildStripped()
		default:
			s.chosen = s.buildWrapped()
		}
	}
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

**File:** common/buildlogger/internal/timestamper/timestamper.go (L261-306)
```go
func (l *Logger) writeHeader(w io.Writer) error {
	if l.timestamp {
		t := now()
		sec := t.Unix()
		buf := l.bufStream

		// Static separators were pre-filled in New(). On a same-second
		// repeat we only refresh the microsecond digits.
		if sec != l.cachedUnix {
			year, month, day := t.Date()
			hour, minute, secOfMin := t.Clock()
			buf[0] = '0' + byte(year/1000)
			buf[1] = '0' + byte((year/100)%10)
			buf[2] = '0' + byte((year/10)%10)
			buf[3] = '0' + byte(year%10)
			buf[5] = '0' + byte(int(month)/10)
			buf[6] = '0' + byte(int(month)%10)
			buf[8] = '0' + byte(day/10)
			buf[9] = '0' + byte(day%10)
			buf[11] = '0' + byte(hour/10)
			buf[12] = '0' + byte(hour%10)
			buf[14] = '0' + byte(minute/10)
			buf[15] = '0' + byte(minute%10)
			buf[17] = '0' + byte(secOfMin/10)
			buf[18] = '0' + byte(secOfMin%10)
			l.cachedUnix = sec
		}

		nanos := t.Nanosecond() / nanosDivisor
		buf[25] = '0' + byte(nanos%10)
		nanos /= 10
		buf[24] = '0' + byte(nanos%10)
		nanos /= 10
		buf[23] = '0' + byte(nanos%10)
		nanos /= 10
		buf[22] = '0' + byte(nanos%10)
		nanos /= 10
		buf[21] = '0' + byte(nanos%10)
		buf[20] = '0' + byte(nanos/10)
	}
	_, err := w.Write(l.bufStream)

	l.bufStream[l.timeLen+3] = byte(FullLineType)

	return err
}
```
