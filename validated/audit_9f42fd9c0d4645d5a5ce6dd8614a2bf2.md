This confirms the vulnerability: the test `TestStepRunnerStream` explicitly documents and asserts (as expected behavior) that `"pre-stamped passthrough does not mask (step-runner masked upstream)"` — with `MaskPhrases: []string{"secret-token"}` and `timestamping: true`, the literal string `"secret-token"` is asserted to appear unmasked in the trace output.

### Title
Step-runner "pre-stamped" passthrough in `StepRunnerStream` bypasses local secret masking entirely - ([File: common/buildlogger/build_logger.go])

### Summary
`Logger.StepRunnerStream` (`common/buildlogger/build_logger.go`, `stepRunnerStream.pickMode`) detects whether the first chunk of data written looks like a step-runner timestamper-formatted line (`isPreStamped`), and if `Timestamping` is enabled, routes all subsequent bytes for that writer's lifetime straight to `l.base` (`s.passthrough = internal.NewSync(l.base)`), completely skipping the `masker`/`urlsanitizer`/`tokensanitizer` wrap chain (`Logger.wrap`). This trust decision is based purely on the byte pattern of the payload, not on any authenticated distinction between step-runner's own bytes and job-influenced content.

### Finding Description
`steps/execute.go`'s `Execute` writes to `opts.Trace` (set to `b.logger.StepRunnerStream(...)` in `common/build.go:564`), which is fed directly by `extended.FollowOutput{Logs: opts.Trace}` inside `c.RunAndFollow(ctx, request, &out)` — i.e., raw output streamed back from the step-runner subprocess for job-defined steps (`opts.Steps []schema.Step`, sourced from CI configuration that an unprivileged pipeline author controls). [1](#0-0) [2](#0-1) 

The `stepRunnerStream.pickMode` logic decides the fate of the *entire* stream based only on whether the **first write** matches `isPreStamped` (checks bytes 4,7,10,13,16,19,26,27 for the timestamp shape) and whether `Timestamping` is on: [3](#0-2) 

When both conditions hold, `s.chosen = s.passthrough`, which writes directly to `l.base` with zero masking applied: [4](#0-3) 

The comment and the accompanying unit test make the trust assumption explicit and confirm it's *intentional* current behavior, not a bug being guarded against: `TestStepRunnerStream`'s subtest `"pre-stamped passthrough does not mask (step-runner masked upstream)"` sets `MaskPhrases: []string{"secret-token"}`, feeds a wire-formatted line containing `"contains secret-token literally\n"`, and asserts the output **still contains** `"secret-token"` unmasked: [5](#0-4) 

The security assumption is that step-runner (a separate trusted component) has already masked secrets before emitting its wire-format lines. However:
- `steps/execute.go`'s `Options` struct passed to `steps.Execute` carries no `MaskPhrases`/`MaskTokenPrefixes` — grepping `steps/*.go` for masking-related fields found none passed into the step-runner request (`NewRequest(opts.JobInfo, opts.Steps)` only carries `JobInfo`/`Steps`).
- The 28-byte header format (`YYYY-MM-DDTHH:MM:SS.UUUUUUZ `) is not any kind of cryptographic/authenticated marker — it is just a literal byte prefix. Any process whose *output* (e.g., a job-defined step's script stdout, printed by the job author) happens to start with that exact 28-byte shape followed by a 2-hex stream ID, a stream-type byte, and a space, will be classified as "pre-stamped, trust it" and routed to bypass the masking chain.
- The runner has no way to verify that step-runner actually enforces its own masking on all output, since step-runner is not given the local Mask Phrases/token prefixes list via `steps.Options` in this codebase.

This means the invariant "no code path may bypass masking regardless of whether data is pre-formatted by step-runner" is violated by design in this code: the passthrough path is a real, reachable path that unconditionally skips `masker.New`/`urlsanitizer.New`/`tokensanitizer.New`.

### Impact Explanation
Any job using the native step-runner integration (`UseNativeSteps`) whose step output — controlled by the CI author (e.g., a script step that `echo`s an interpolated `CI_JOB_TOKEN` or a masked variable) — reaches this writer, will have that value flow into the job trace/log artifact completely unmasked, once the passthrough mode is latched by the first write of the stream. Since `pickMode` runs `sync.Once` and locks in `s.chosen` for the writer's entire lifetime, a single crafted "pre-stamped-looking" first write forces every subsequent chunk (even ordinary lines) through the unmasked passthrough for that stream instance. Concrete scoped impact: masked secret (e.g. `CI_JOB_TOKEN`) exposed verbatim in the job trace artifact, viewable by anyone with read access to the job log.

### Likelihood Explanation
Preconditions: runner configured to use native step-runner execution (`b.UseNativeSteps()` true, feature currently used for step-dispatch stages), `Timestamping` enabled in `buildlogger.Options` (the normal/default trace mode), and a job author who can define step output. Given step-runner emits its own lines in exactly this wire format for its own normal output (per the doc comment: "step-runner can emit log lines pre-formatted in the runner's timestamper format"), any step (including a simple `script`-shim step) whose stdout is relayed by step-runner using this format will already qualify for passthrough — this is not a narrow edge case requiring an attacker to forge an unusual byte sequence; it is the common code path for step-runner-driven jobs. If step-runner does not itself replicate the runner's exact mask-phrase/token-prefix list for every value the job later interpolates or echoes, unmasked secrets will reach the trace. This is feasible and repeatable via a normal CI job.

### Recommendation
Do not trust wire-format bytes based on shape alone to bypass local masking. Either:
1. Always demux pre-stamped step-runner lines through `innerstream.Splitter` and route inner bodies through the standard `wrap` chain (as already done for the `isPreStamped && !timestamping` branch) regardless of the `Timestamping` setting, re-stamping only if necessary to avoid double timestamps (e.g., strip and reuse the inner timestamp rather than skipping masking).
2. Alternatively, have Runner supply `MaskPhrases`/`MaskTokenPrefixes` to step-runner via the request and require step-runner to guarantee identical masking, then add integration tests that verify step-runner actually masks every configured phrase/token before instructing Runner to treat step-runner output as pre-masked — with fallback to local masking if this contract cannot be proven per-line.

### Proof of Concept
Existing test already demonstrates this precisely — it should be treated as a regression test to *fix*, not a confirmation of correct behavior: [5](#0-4) 

Differential PoC test (Go):
```go
func TestStepRunnerStream_MaskingParity(t *testing.T) {
    jt := newFakeJobTrace()
    l := newStepRunnerLogger(t, jt, true /*timestamping*/, "AGP-secrettoken123")

    w := l.StepRunnerStream(StreamWorkLevel, Stdout)
    // Attacker-controlled step output that happens to start with a
    // wire-format-shaped header and contains the masked secret.
    _, _ = w.Write([]byte(wireLine('O', "leaked: AGP-secrettoken123\n")))
    _ = w.Close()

    out := jt.Read()
    // EXPECTED (fixed behavior): secret must be masked regardless of stream origin.
    assert.NotContains(t, out, "AGP-secrettoken123")
    assert.Contains(t, out, "[MASKED]")
}
```
Currently this assertion fails (the secret leaks through), matching the sibling test's explicit acknowledgment that "pre-stamped passthrough does not mask."

### Citations

**File:** steps/execute.go (L94-97)
```go
	//nolint:errcheck
	defer c.CloseConn()

	out := extended.FollowOutput{Logs: opts.Trace}
```

**File:** common/build.go (L560-576)
```go
			// step-runner can emit log lines pre-formatted in the runner's
			// timestamper format (timestamp + stream marker + masking applied
			// upstream). StepRunnerStream passes pre-formatted data straight
			// through, otherwise the local wrap chain is applied.
			stdout := b.logger.StepRunnerStream(buildlogger.StreamWorkLevel, buildlogger.Stdout)
			defer stdout.Close()

			return wrapStepStageErr(steps.Execute(ctx, steps.Options{
				Connector: connector,
				JobInfo: steps.JobInfo{
					ID:         b.ID,
					Timeout:    b.GetBuildTimeout(),
					ProjectDir: b.FullProjectDir(),
					Variables:  b.GetAllVariables(),
				},
				Steps:          req,
				Trace:          stdout,
```

**File:** common/buildlogger/build_logger.go (L118-120)
```go
	return &stepRunnerStream{
		timestamping: l.timestamping,
		passthrough:  internal.NewSync(l.base),
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
