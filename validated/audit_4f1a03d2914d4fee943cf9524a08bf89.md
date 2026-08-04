### Title
Step-runner pre-stamped passthrough heuristic lets an unprivileged job forge output that bypasses all Runner-side masking - (File: common/buildlogger/build_logger.go)

### Summary
`StepRunnerStream` decides, on the very first write, whether to treat a byte stream as "pre-stamped" step-runner output using only a positional byte pattern (`isPreStamped`), and if so with `Timestamping` on, routes all bytes straight to the base trace with zero masking applied. Since the bytes reaching this stream originate from commands the job author controls (a step's `run`/script output), a normal pipeline author can forge a line matching the pattern and get Runner's masker/urlsanitizer/tokensanitizer entirely skipped for that log line and every subsequent write on the same stream.

### Finding Description
`isPreStamped` only checks 8 fixed byte positions of the buffer for delimiter characters (`-`, `T`, `:`, `.`, `Z`, ` `), it does not authenticate that the data actually came from the trusted step-runner binary: [1](#0-0) . `StepRunnerStream`'s `pickMode` uses this heuristic to choose between three code paths, and when `isPreStamped(p) && s.timestamping` is true, the chosen writer is `s.passthrough` — a bare `internal.NewSync(l.base)` with none of the masking/sanitizing wrap chain (`masker.New`, `urlsanitizer.New`, `tokensanitizer.New`) applied: [2](#0-1) [3](#0-2) . Compare that to the normal `wrap()` path, which always applies masker/urlsanitizer/tokensanitizer before writing to the base trace: [4](#0-3) .

The mode is picked once via `sync.Once` on the first `Write`, and persists for the life of the stream — a later write on the same stream that no longer looks pre-stamped is *still* sent through the already-chosen (unmasked) passthrough writer, as the codebase's own test explicitly documents ("Only one 'Z ' … the trailing write wasn't re-stamped because the stream is in passthrough"): [5](#0-4) .

The repository's own test suite already codifies the exact unsafe trust assumption named in the question — that passthrough is safe because "step-runner masked upstream" — yet the assertion only checks that the content is *not stamped twice*, not that a Runner-masked secret embedded in the payload is actually redacted: it explicitly asserts the masked phrase `secret-token` still appears in the output for the pre-stamped/timestamping-on case: [6](#0-5) . This is the exact opposite of the sibling non-pre-stamped case, which correctly masks the same phrase: [7](#0-6) .

Exploit flow: a pipeline author defines a CI/CD "step" (routed through Runner's step-runner/ProxyExec execution path) whose script's very first stdout bytes are crafted to satisfy `isPreStamped`'s 8-byte pattern check (any bytes are permitted elsewhere, e.g. `"2024-01-01T00:00:00.000000Z 01O "` followed by a Runner-masked CI/CD variable value, then a newline). Because Runner routes the step's raw combined output through `Logger.StepRunnerStream`, and `Timestamping` is commonly enabled for job traces, `pickMode` selects `s.passthrough`, and the masked secret is written verbatim to the job log with no masker/tokensanitizer/urlsanitizer applied — for that line and, per `TestStepRunnerStream_ChoicePersists`, for the remainder of that stream.

### Impact Explanation
An unprivileged pipeline author who uses the `steps:`/step-runner execution feature can fully bypass Runner's masking pipeline (phrase masking, URL parameter masking, and token-prefix sanitizing) for an entire log stream by prefixing its first output line with a forged 28-byte pseudo-timestamp header. This defeats the "masked values must never leak" invariant and reveals CI/CD masked variables, `CI_JOB_TOKEN`-derived tokens, or other masked secrets verbatim in the job log/trace, which is visible to anyone with pipeline/log read access on the project.

### Likelihood Explanation
Preconditions are low-bar and fully within an unprivileged job author's control: (1) the job uses the step-runner/ProxyExec-routed step execution path, a normal CI feature, not an admin misconfiguration; (2) trace timestamping is enabled, which is the default behavior in current GitLab Runner. No special permissions, executor escape, or admin action is required — only crafting the first bytes of a step's own script output, which the job author already fully controls. The behavior is deterministic and repeatable on every run.

### Recommendation
Do not trust a positional byte-pattern heuristic as a security/masking-bypass decision. Options: (a) always run pre-stamped step-runner output through the masking stages (masker/urlsanitizer/tokensanitizer) even in "passthrough" mode, only skipping the *timestamper* stage to avoid double-stamping; (b) authenticate step-runner's stream via an explicit out-of-band signal (e.g., a dedicated file descriptor, protocol framing, or a signed/negotiated marker) instead of inferring trust from guessable content; or (c) re-scan passthrough bytes for masked phrases/tokens before writing to the base trace, rather than assuming upstream masking occurred.

### Proof of Concept
Go unit test extending `common/buildlogger/build_logger_step_runner_test.go`:
```go
func TestStepRunnerStream_ForgedPreStampDoesNotBypassMasking(t *testing.T) {
    jt := newFakeJobTrace()
    l := newStepRunnerLogger(t, jt, true, "MASKED_VALUE") // timestamping on, mask phrase configured

    w := l.StepRunnerStream(StreamWorkLevel, Stdout)
    // Attacker-controlled step script output: forged 28-byte header + real masked secret.
    _, err := w.Write([]byte(wireLine('O', "leaking MASKED_VALUE now\n")))
    require.NoError(t, err)
    require.NoError(t, w.Close())

    out := jt.Read()
    assert.NotContains(t, out, "MASKED_VALUE", "masked value must never leak, even via forged pre-stamp passthrough")
}
```
Expected today: this test fails (the secret is present verbatim), demonstrating the bypass. A PoC job would define a `steps:`-based job whose step script does `printf '2024-01-01T00:00:00.000000Z 01O leaking-%s\n' "$MASKED_VAR"` and assert the resulting job log contains the raw variable value instead of `[MASKED]`.

### Citations

**File:** common/buildlogger/build_logger.go (L113-131)
```go
func (l *Logger) StepRunnerStream(streamID int, streamType StreamType) io.WriteCloser {
	if l.base == nil {
		return internal.NewNopCloser(io.Discard)
	}

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

**File:** common/buildlogger/build_logger_step_runner_test.go (L60-67)
```go
		"pre-stamped passthrough does not mask (step-runner masked upstream)": {
			timestamping: true,
			maskPhrases:  []string{"secret-token"},
			input:        wireLine('O', "contains secret-token literally\n"),
			assertion: func(t *testing.T, output string) {
				assert.Contains(t, output, "secret-token")
			},
		},
```

**File:** common/buildlogger/build_logger_step_runner_test.go (L68-75)
```go
		"pre-stamped strip applies the wrap chain masker": {
			maskPhrases: []string{"secret-token"},
			input:       wireLine('O', "contains secret-token literally\n"),
			assertion: func(t *testing.T, output string) {
				assert.Contains(t, output, "[MASKED]")
				assert.NotContains(t, output, "secret-token")
			},
		},
```

**File:** common/buildlogger/build_logger_step_runner_test.go (L128-147)
```go
// First write locks in the mode; subsequent writes follow the same path
// even if they happen not to look pre-stamped.
func TestStepRunnerStream_ChoicePersists(t *testing.T) {
	jt := newFakeJobTrace()
	l := newStepRunnerLogger(t, jt, true)

	w := l.StepRunnerStream(StreamWorkLevel, Stdout)
	_, err := w.Write([]byte(wireLine('O', "first\n")))
	require.NoError(t, err)
	_, err = w.Write([]byte("trailing-without-stamp\n"))
	require.NoError(t, err)
	require.NoError(t, w.Close())

	out := jt.Read()
	assert.Contains(t, out, "first")
	assert.Contains(t, out, "trailing-without-stamp\n")
	// Only one 'Z ' (the inner stamp on the first write); the trailing
	// write wasn't re-stamped because the stream is in passthrough.
	assert.Equal(t, 1, strings.Count(out, "Z "))
}
```
