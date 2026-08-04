### Title
Masked phrase split across a stdout/stderr frame boundary evades masking in the step-runner demux path - (File: common/buildlogger/build_logger.go, common/buildlogger/innerstream/innerstream.go)

### Summary
`Logger.StepRunnerStream`'s stripped mode creates two *independent* mask chains — one for the demuxed stdout body and one for the demuxed stderr body — that both funnel into the same underlying trace (`l.base`). Because each `masker` instance tracks phrase-match state only within its own stream, an attacker who controls which OS stream (stdout vs stderr) each byte of a masked secret is written to can split the secret exactly at a stdout/stderr boundary so that neither masker ever sees the complete phrase, letting the full secret appear unmasked, in order, in the shared trace.

### Finding Description
`Logger.StepRunnerStream` picks "stripped" mode when the first bytes look pre-stamped and timestamping is off: [1](#0-0) 

`buildStripped` builds **two separate** `l.wrap(...)` chains — one keyed to `Stdout`, one keyed to `Stderr` — and hands them to `innerstream.New`: [2](#0-1) 

`innerstream.Splitter.consumeLine` decodes each physical line's `streamType` byte and routes the decoded body to `s.stdout` or `s.stderr` accordingly — two completely separate `io.Writer` values, each with its own mask chain instance: [3](#0-2) 

The masker is explicitly designed to detect a phrase split across multiple `Write()` calls *to the same masker instance*, by keeping `matching` state on the instance: [4](#0-3) [5](#0-4) 

However, because the stdout mask chain and the stderr mask chain are two *distinct* `masker` instances (each created fresh inside `l.wrap`), a phrase spanning a stdout-tagged line and a stderr-tagged line is split between two independent state machines. Neither one ever accumulates the full phrase, so `m.matching` never reaches `len(m.phrase)` in either chain, and each half is forwarded to the next writer (`urlsanitizer` → `tokensanitizer` → the underlying trace) unmodified. Both chains ultimately write to the same `l.base` trace writer, so the two unmasked halves appear consecutively in the trace, reconstructing the full secret in cleartext.

The attacker precondition stated in the question — control of step-runner step output/framing bytes — is sufficient: step-runner's own timestamper stamps whatever the job script writes to its stdout/stderr file descriptors with the corresponding `streamType` byte (`'O'`/`'E'`) per the documented wire format. A script under step-runner can trivially interleave two `write()` calls, one to fd 1 and one to fd 2, splitting a known masked value (e.g., a masked CI/CD variable) across the boundary, without needing to fabricate the framing itself — step-runner produces the real framing honestly, based on which fd the script used.

### Impact Explanation
A masked secret (CI/CD variable value, or other configured mask phrase) can be leaked verbatim into the job trace when it is emitted via a step-runner step, defeating the masking guarantee scoped as: "every byte that reaches the trace for a masked-secret-bearing stream must pass through the full mask/sanitizer chain." The secret does pass through *a* mask chain, but the split-state design allows the phrase-matching invariant to be defeated, resulting in concrete, attacker-controlled secret disclosure via the standard job trace.

### Likelihood Explanation
Highly feasible and repeatable: the attacker only needs a job that runs a step via step-runner and writes a known/predictable masked value in two writes, one to stdout and one to stderr, split at the desired boundary. No timing race or malformed/adversarial framing bytes are required — the legitimate step-runner timestamper produces the exact frame layout needed for the exploit as a side effect of normal stdout/stderr writes. The `Timestamping` config must be off (or the runner's default trace timestamping disabled) for the "stripped" branch to be selected, which is a realistic and common Runner configuration.

### Recommendation
Unify the mask/sanitizer state across the demuxed stdout and stderr sub-streams for a given `StepRunnerStream`/streamID — e.g., share a single `masker`/`urlsanitizer`/`tokensanitizer` chain (or a shared matching state) between the two `l.wrap` invocations in `buildStripped`, rather than instantiating two independent chains that happen to write to the same base trace. Alternatively, merge stdout/stderr bodies into a single ordered writer before applying the mask chain, so a phrase split across stream-type boundaries is still detected as if it were a single stream.

### Proof of Concept
Go unit test outline (extends `common/buildlogger/innerstream/innerstream_test.go` or `build_logger_step_runner_test.go`):

```go
func TestStepRunnerStream_MaskBypassAcrossStreamBoundary(t *testing.T) {
    var trace bytes.Buffer
    secret := "s3cr3t-token"
    logger := New(&fakeTrace{&trace}, entry, Options{
        MaskPhrases:  []string{secret},
        Timestamping: false,
    })
    w := logger.StepRunnerStream(0, Stdout)

    ts := "2024-01-01T00:00:00.000000Z "
    half1, half2 := secret[:6], secret[6:]

    // frame 1: stdout, body = first half of secret
    _, _ = w.Write([]byte(ts + "00O " + half1 + "\n"))
    // frame 2: stderr, body = second half of secret
    _, _ = w.Write([]byte(ts + "00E " + half2 + "\n"))
    _ = w.Close()

    // Assertion: the secret must never appear unmasked in the trace.
    require.NotContains(t, trace.String(), secret)
    require.Contains(t, trace.String(), "[MASKED]") // expected if fixed
}
```
Expected current (buggy) behavior: `trace.String()` contains the literal secret `"s3cr3t-token"` (reconstructed from the two adjacent unmasked halves), and no `[MASKED]` replacement occurs — confirming masking bypass via the stdout/stderr split path.

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

**File:** common/buildlogger/innerstream/innerstream.go (L105-122)
```go
	streamType := line[streamTypeOff]
	lineType := line[lineTypeOff]
	body := line[headerLen:]

	pending, w := &s.pendingStdout, s.stdout
	if streamType == streamStderr {
		pending, w = &s.pendingStderr, s.stderr
	}

	if len(*pending) > 0 {
		out := *pending
		if lineType == linePartial && out[len(out)-1] == '\n' {
			out = out[:len(out)-1]
		}
		if _, err := w.Write(out); err != nil {
			return err
		}
	}
```

**File:** common/buildlogger/internal/masker/masker.go (L48-52)
```go
type masker struct {
	phrase   []byte
	matching int
	next     io.WriteCloser
}
```

**File:** common/buildlogger/internal/masker/masker.go (L70-99)
```go
		if m.matching == 0 {
			off := bytes.IndexByte(p[n:], m.phrase[0])
			if off < 0 {
				n += len(p[n:])
				break
			}
			if off > -1 {
				n += off
			}
		}

		// find out how much data we can match: the minimum of len(p) and the
		// remainder of the phrase.
		min := len(m.phrase[m.matching:])
		if len(p[n:]) < min {
			min = len(p[n:])
		}

		// try to match the next part of the phrase
		if bytes.HasPrefix(p[n:], m.phrase[m.matching:m.matching+min]) {
			// send any data that we've not sent prior to our match to the
			// next writer.
			_, err = m.next.Write(p[last:n])
			if err != nil {
				return n, err
			}

			m.matching += min
			n += min
			last = n
```
