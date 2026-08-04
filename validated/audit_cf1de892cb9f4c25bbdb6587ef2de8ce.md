### Title
Weak `isPreStamped` header heuristic allows step-runner content to bypass the masker/tokensanitizer/urlsanitizer chain via `Logger.StepRunnerStream` passthrough - (File: common/buildlogger/build_logger.go)

### Summary
`stepRunnerStream.pickMode` decides, on the very first `Write`, whether all subsequent bytes for that stream go through gitlab-runner's own masking chain or straight to `l.base` unmasked. The decision is based solely on an 8-byte positional heuristic (`isPreStamped`) that checks only fixed separator characters, not a cryptographic or structurally-enforced marker, and the passthrough path is justified purely by the comment/assumption "step-runner masked upstream" which is not verified anywhere in this code.

### Finding Description
`Logger.StepRunnerStream` (common/buildlogger/build_logger.go:113) wraps step-runner output in a `stepRunnerStream` whose `Write` calls `once.Do(s.pickMode(p))` on the first chunk written [1](#0-0) . `pickMode` chooses `s.passthrough = internal.NewSync(l.base)` — bypassing `masker`, `tokensanitizer`, and `urlsanitizer` entirely — whenever `isPreStamped(p) && s.timestamping` is true [2](#0-1) .

`isPreStamped` only validates 8 fixed byte offsets (separators `-`, `-`, `T`, `:`, `:`, `.`, `Z`, ` `) and does not validate that the surrounding bytes are actually digits, a valid stream-id, or any other structural property of a real step-runner frame [3](#0-2) . The docstring for `StepRunnerStream` and the accompanying unit test explicitly document that this path is trusted to already be masked ("`step-runner masked upstream`"), and the test asserts that a masked phrase configured on `Logger` (`l.maskPhrases`) is *not* applied when a chunk matches the pre-stamped shape and timestamping is enabled [4](#0-3) .

The bypass decision is made once per stream instance and is sticky for the lifetime of that `io.WriteCloser` (`sync.Once`), so any bytes written after the first chunk — including bytes containing a masked variable value that gitlab-runner itself is responsible for masking (`l.maskPhrases`, `l.maskTokenPrefixes`) — are also never passed through `masker`/`tokensanitizer`/`urlsanitizer` once passthrough mode is locked in [2](#0-1) . There is no verification anywhere in this codebase that step-runner actually applies gitlab-runner's specific mask-phrase list (`Options.MaskPhrases`, derived from the job's protected/masked CI variables) before emitting this wire-format-shaped output; the trust is purely assumptive.

### Impact Explanation
If step-runner's own output-sanitization does not cover the exact set of values gitlab-runner is configured to mask (job-level masked variables passed via `Options.MaskPhrases`), any content that reaches the passthrough path — a full stream once the first chunk matches the pre-stamped shape — is emitted to the trace/log verbatim, with gitlab-runner's mask-phrase, token-prefix, and URL-parameter sanitization completely skipped for that entire stream. This matches the scoped impact: masked/protected values reaching `l.base` (and thus the job trace visible to any trace viewer) unmasked, for the full duration of that stream.

### Likelihood Explanation
Requires the `FF_.../Timestamping` feature flag/option enabled and a job using step-runner (custom `run:`/steps or `include:`-referenced steps), both of which are standard, unprivileged, job-author-controlled configurations. Because `isPreStamped` only checks separator bytes and not digit ranges or any authenticated marker, and because the masking-skip is a blanket, sticky decision for the whole stream, the surface for content that isn't actually pre-masked by step-runner (e.g., because step-runner's masking doesn't know about gitlab-runner's job-specific mask-phrase list) reaching this passthrough path is plausible without requiring an exact forged header — genuine step-runner-framed lines containing an as-yet-unmasked secret in the body are sufficient.

### Recommendation
Do not rely on a positional byte heuristic as an authorization boundary for skipping the masking chain. Either (a) always run step-runner-passthrough content through gitlab-runner's own `masker`/`tokensanitizer`/`urlsanitizer` regardless of the pre-stamped shape (applying them to the "body" segment after the header rather than skipping entirely), or (b) require an authenticated/structural guarantee (e.g., a dedicated out-of-band signal from the step-runner integration, not sniffed from the byte stream) before trusting that content was already masked upstream.

### Proof of Concept
Extend `common/buildlogger/build_logger_step_runner_test.go` with a case where `timestamping: true` and a mask phrase is configured, but the input's body (after the pre-stamped header) contains the value produced by `wireLine('O', "echo of $CI_MASKED_VAR -> secret-token\n")`. Assert (as the existing test at lines 60-67 already effectively documents) that `jt.Read()` still contains `"secret-token"` in cleartext — demonstrating that any content shaped like a step-runner frame skips `l.maskPhrases` masking, independent of whether the value was actually sanitized by step-runner itself.

### Citations

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
