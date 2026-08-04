### Title
Attacker-forgeable timestamp header lets a job step force `stepRunnerStream` into `passthrough` mode, bypassing all local masking - (File: common/buildlogger/build_logger.go)

### Summary
`stepRunnerStream.pickMode` decides, once per stream and based purely on a byte-pattern heuristic (`isPreStamped`), whether output is trusted step-runner-stamped data (routed to `s.passthrough`, which applies **no** masking at all) or untrusted data (routed through the full mask/tokensanitizer/urlsanitizer chain). Because the heuristic only inspects content bytes and not provenance, a job step that can influence the very first bytes written to this stream can forge a fake timestamp header and force the zero-masking `passthrough` path for the entire lifetime of the stream.

### Finding Description
`Logger.StepRunnerStream` (`common/buildlogger/build_logger.go:113`) builds a `stepRunnerStream` with three possible destinations: `passthrough` (`internal.NewSync(l.base)` — raw, unwrapped, unmasked), `buildStripped` (demuxes via `innerstream.Splitter` but still routes bodies through `l.wrap`, i.e. through masker/urlsanitizer/tokensanitizer), and `buildWrapped` (`l.wrap` directly). `pickMode` (lines 167-178) chooses between them using only `isPreStamped(p)`: [1](#0-0) 

`isPreStamped` validates only that specific byte offsets contain `'-'`, `'T'`, `':'`, `'.'`, `'Z'`, `' '` — a pattern fully specifiable by anyone who controls the raw bytes of the stream, since all digit positions are unconstrained: [2](#0-1) 

The design intent (per the comments and `innerstream` package docs) is that step-runner's own internal timestamper stamps *its builtins'* output, while other data sources feeding the same `FollowOutput.Logs` writer (`steps/execute.go:97`) are not pre-stamped and must go through the local wrap chain: [3](#0-2) [4](#0-3) 

Because both stamped and unstamped content share the same `io.Writer` interface into `stepRunnerStream.Write`, and classification happens exactly once via `sync.Once` on the first `Write()` call (`build_logger.go:155-158`), an attacker who controls a job step's raw output (e.g., a step that writes arbitrary bytes to stdout/stderr, which step-runner forwards without re-stamping) can prefix output with a crafted 28-byte string matching `isPreStamped`'s exact positions (e.g. `"2024-01-01T00:00:00.000000Z "`). If `Timestamping` is enabled, this forces `s.chosen = s.passthrough`, which is a bare `internal.NewSync(l.base)` — completely bypassing `masker`, `urlsanitizer`, and `tokensanitizer` for every subsequent `Write()` on that stream instance, including later writes containing masked secret values or CI/CD tokens.

Existing protections do not stop this: `isPreStamped` performs no cryptographic, out-of-band, or provenance check — it is a pure content heuristic operating on attacker-reachable bytes, and the decision is latched via `sync.Once` for the whole stream's lifetime, so one crafted first write compromises all following writes.

### Impact Explanation
If exploited, masked phrases (`MaskPhrases`), token prefixes (`MaskTokenPrefixes`/`tokensanitizer`), and URL-embedded secrets that would normally be redacted by `masker`/`tokensanitizer`/`urlsanitizer` are instead written verbatim to the job's visible trace log via the `passthrough` writer, exposing secret values/tokens to anyone who can view that job's log (the job's own author/project, per scoped impact).

### Likelihood Explanation
Feasible and repeatable: the attacker only needs a job step whose output is forwarded through this code path with `Timestamping` enabled and needs to control the first bytes written to the underlying stream (a normal, unprivileged capability — writing arbitrary text to stdout/stderr from a step). The exact byte positions required are static and easily reproduced by any pipeline author with `echo`-style output; no race condition or timing skew is needed since `sync.Once` deterministically locks in the classification from the first write.

### Recommendation
Do not rely on unauthenticated content-based heuristics to decide whether to skip masking. Either: (1) always route step-runner output through the full wrap chain (`buildWrapped`/`buildStripped`) regardless of `isPreStamped`, accepting double-stamping instead of skipping masking, or (2) require an explicit, out-of-band signal from step-runner (e.g., a dedicated gRPC/control message or a distinct stream not fed by attacker-writable job content) to indicate the data is genuinely step-runner-stamped, rather than inferring trust from the payload itself. At minimum, `passthrough` mode should never fully bypass `masker`/`tokensanitizer`/`urlsanitizer` — these should still run even in the pre-stamped case.

### Proof of Concept
Go unit test sketch for `common/buildlogger`:
```go
func TestStepRunnerStream_ForgedHeaderBypassesMasking(t *testing.T) {
    var base bytes.Buffer
    logger := New(&fakeTrace{&base}, entry, Options{
        MaskPhrases:  []string{"supersecret"},
        Timestamping: true,
    })
    w := logger.StepRunnerStream(0, Stdout)

    // Attacker-controlled job step output: fake header + secret.
    forged := []byte("2024-01-01T00:00:00.000000Z supersecret\n")
    w.Write(forged)
    w.Close()

    // Assert masking was bypassed (bug present) vs. redacted (fixed).
    assert.NotContains(t, base.String(), "supersecret") // should hold true after fix
}
```
Expected today: `base.String()` contains `"supersecret"` unredacted because `isPreStamped` returns true and `pickMode` selects `s.passthrough`, proving the bypass. After a fix, the secret should always be masked regardless of the forged header.

### Citations

**File:** common/buildlogger/build_logger.go (L146-178)
```go
func isPreStamped(p []byte) bool {
	if len(p) < 28 {
		return false
	}
	return p[4] == '-' && p[7] == '-' && p[10] == 'T' &&
		p[13] == ':' && p[16] == ':' && p[19] == '.' &&
		p[26] == 'Z' && p[27] == ' '
}

func (s *stepRunnerStream) Write(p []byte) (int, error) {
	s.once.Do(s.pickMode(p))
	return s.chosen.Write(p)
}

func (s *stepRunnerStream) Close() error {
	// Close before any write: fall back to the wrap chain so Close has
	// something to delegate to.
	s.once.Do(s.pickMode(nil))
	return s.chosen.Close()
}

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

**File:** steps/execute.go (L95-97)
```go
	defer c.CloseConn()

	out := extended.FollowOutput{Logs: opts.Trace}
```

**File:** common/buildlogger/innerstream/innerstream.go (L1-12)
```go
// Package innerstream parses the wire format the inner step-runner's
// timestamper emits and demuxes its content back into separate stdout and
// stderr writers. The outer step-runner re-stamps everything its builtins
// write, so without this every nested log line would carry two stacked
// timestamps.
//
// Header layout (see step-runner's internal/streamer/timestamper):
//
//	bytes 0..27  "YYYY-MM-DDTHH:MM:SS.uuuuuuZ "  (timestamp + space)
//	bytes 28..29 stream id (2 hex digits)
//	byte  30     stream type   ('O' = stdout, 'E' = stderr)
//	byte  31     line type     (' ' = full,   '+' = continuation)
```
