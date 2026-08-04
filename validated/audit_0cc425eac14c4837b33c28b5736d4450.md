### Title
Nested `run:` dispatch omits the `Masked` flag on forwarded variables, allowing masked CI variables to leak in cleartext trace output when Timestamping is enabled - ([File: functions/concrete/run/run_steps.go])

### Summary
`buildRunRequest` in `functions/concrete/run/run_steps.go` forwards `r.env.JobVars` to the nested step-runner as `client.Variable{Key: key, Value: value.GetStringValue()}`, never setting the `Masked` field. This is inconsistent with the top-level dispatch path (`steps/steps.go`'s `addVariables`), which explicitly forwards `Masked: v.Masked`. Because the outer log pipeline (`common/buildlogger`) trusts pre-stamped step-runner output as already masked when Timestamping is on, the missing `Masked` flag on the nested request breaks that trust chain and lets a masked variable's real value reach the trace unredacted.

### Finding Description
`r.env.JobVars` (type `map[string]*structpb.Value`) holds the real (unmasked) values of job variables — necessary because scripts need the actual secret to run, e.g. `docker login`. Masking is enforced purely at the trace-writing layer via `masker.New` using phrases derived from `b.GetAllVariables().Masked()` [1](#0-0) .

`buildRunRequest` builds the nested `run:` request's `Variables` from `r.env.JobVars` without ever setting `Masked`: [2](#0-1) 

Compare this to the top-level (non-nested) dispatch path, which forwards the `Masked` flag from `spec.Variable`: [3](#0-2) 

The reason the `Masked` flag matters: `common/build.go`'s `executeStepStage` routes step-runner-produced output through `b.logger.StepRunnerStream`, which — when the data is "pre-stamped" (wire-formatted by a step-runner) and `Timestamping` is enabled — passes the bytes straight to the trace **without applying the masker**, trusting that "step-runner masked upstream" already: [4](#0-3) [5](#0-4) 

This trust assumption is explicitly pinned in a test: pre-stamped passthrough data is *not* masked by the outer wrap chain because the design assumes the producing step-runner already masked it (using the `Masked` flags it was given): [6](#0-5) 

The nested run: dispatch path (`runUserSteps` → `buildRunRequest` → `cli.RunAndFollow`) sends `Variables` to a second, nested step-runner instance dialed over the same unix socket, decoded via `innerstream.New(r.env.Stdout, r.env.Stderr)` and forwarded to `r.env.Stdout`/`Stderr` — the same pipe the hosting step-runner re-stamps and forwards up to gitlab-runner's `build.go`: [7](#0-6) [8](#0-7) 

Since the nested step-runner never receives the `Masked` flag for any variable, it has no signal to redact a masked value before emitting its own pre-stamped output. When that pre-stamped output later reaches `StepRunnerStream` in passthrough mode (Timestamping on), gitlab-runner's own masker is skipped entirely, since the design intentionally defers masking to the (now uninformed) upstream step-runner. The result: a masked/protected CI variable echoed inside a `run:` step's script can appear unredacted in the job trace.

### Impact Explanation
An unprivileged pipeline author who can define `.gitlab-ci.yml` steps using the `run:` keyword can echo any masked/protected CI variable available to the job (e.g. `echo "$CI_REGISTRY_PASSWORD"`) inside a nested step. If the runner has the Timestamping feature flag (`FF_USE_TIMESTAMPS`, i.e. `UseTimestamps`) enabled, the masked secret's value is exposed in cleartext in the persisted job trace/log, which is readable by anyone with job-log read access — a direct violation of the masked-value invariant, scoped exactly to nested-run trace output.

### Likelihood Explanation
Preconditions: the Concrete step-runner path must be active (`FF_USE_CONCRETE`/UseConcrete), the job must use the `run:` keyword, at least one masked variable must be job-visible, and `Timestamping` must be enabled. These are ordinary runner configuration flags, not admin-privilege-gated secrets, and the attacker only needs the ability to write pipeline YAML with a `run:` step — a standard unprivileged pipeline-author capability. The bug is deterministic and repeatable given these flags; it is not a race condition or timing issue.

### Recommendation
Forward the `Masked` flag (and any other masking-relevant metadata) from the job's variable specs into the nested `client.Variable` entries built in `buildRunRequest`, mirroring `steps/steps.go`'s `addVariables`. Since `r.env.JobVars` (`structpb.Value`) currently carries no masked flag, `env.Env` needs a masked-variable set/lookup (e.g., populated from `spec.Variable.Masked` at `Runner.New` time) so `buildRunRequest` can populate `client.Variable.Masked` correctly for every forwarded variable, restoring the trust invariant that pre-stamped passthrough output has genuinely been masked upstream.

### Proof of Concept
Unit test in `functions/concrete/run/run_steps_test.go` extending the existing `TestBuildRunRequest_ForwardsJobVars` pattern:
```go
func TestBuildRunRequest_ForwardsMaskedFlag(t *testing.T) {
    name, script := "step", "true"
    r := &Runner{
        config: &Config{ID: 42},
        env: &env.Env{
            WorkingDir: "/work",
            JobVars: map[string]*structpb.Value{
                "CI_REGISTRY_PASSWORD": structpb.NewStringValue("super-secret"),
            },
            // Env would need a way to mark CI_REGISTRY_PASSWORD as masked,
            // e.g. via a MaskedVars set populated from spec.Variable.Masked.
        },
    }

    req, err := r.buildRunRequest([]schema.Step{{Name: &name, Script: &script}})
    require.NoError(t, err)

    for _, v := range req.Variables {
        if v.Key == "CI_REGISTRY_PASSWORD" {
            assert.True(t, v.Masked, "masked variable must be forwarded to nested step-runner as Masked=true")
        }
    }
}
```
Integration-level assertion (mirroring `common/buildtest/masking.go`'s `RunBuildWithMasking`): configure a build with `FF_USE_CONCRETE` and `UseTimestamps` on, a masked variable `MASKED_KEY=MASKED_VALUE`, and a job step using `run:` that echoes `$MASKED_KEY`; assert the captured trace does **not** contain `MASKED_VALUE` and does contain `[MASKED]`, matching the existing pattern in `common/buildtest/masking.go` lines 141-146.

### Citations

**File:** common/build.go (L560-566)
```go
			// step-runner can emit log lines pre-formatted in the runner's
			// timestamper format (timestamp + stream marker + masking applied
			// upstream). StepRunnerStream passes pre-formatted data straight
			// through, otherwise the local wrap chain is applied.
			stdout := b.logger.StepRunnerStream(buildlogger.StreamWorkLevel, buildlogger.Stdout)
			defer stdout.Close()

```

**File:** common/build.go (L1637-1648)
```go
func (b *Build) getNewLogger(trace JobTrace, log *logrus.Entry, teeOnly bool) buildlogger.Logger {
	return buildlogger.New(
		trace,
		log,
		buildlogger.Options{
			MaskPhrases:          b.GetAllVariables().Masked(),
			MaskTokenPrefixes:    b.Job.Features.TokenMaskPrefixes,
			Timestamping:         b.IsFeatureFlagOn(featureflags.UseTimestamps),
			MaskAllDefaultTokens: b.IsFeatureFlagOn(featureflags.MaskAllDefaultTokens),
			TeeOnly:              teeOnly,
		},
	)
```

**File:** functions/concrete/run/run_steps.go (L53-75)
```go
func (r *Runner) runUserSteps(ctx context.Context, steps []schema.Step) error {
	req, err := r.buildRunRequest(steps)
	if err != nil {
		return err
	}

	dialer := unixSocketDialer(socketPath())
	cli, err := extended.New(dialer)
	if err != nil {
		return fmt.Errorf("dialing step-runner: %w", err)
	}
	//nolint:errcheck
	defer cli.CloseConn()

	splitter := innerstream.New(r.env.Stdout, r.env.Stderr)
	out := &extended.FollowOutput{Logs: splitter}

	status, err := cli.RunAndFollow(ctx, req, out)
	// Flush any line whose continuation marker we never saw.
	flushErr := splitter.Flush()

	return interpretRunResult(status, err, flushErr)
}
```

**File:** functions/concrete/run/run_steps.go (L94-97)
```go
	variables := make([]client.Variable, 0, len(r.env.JobVars))
	for key, value := range r.env.JobVars {
		variables = append(variables, client.Variable{Key: key, Value: value.GetStringValue()})
	}
```

**File:** steps/steps.go (L47-62)
```go
func addVariables(vars spec.Variables) []client.Variable {
	result := make([]client.Variable, 0, len(vars))
	for _, v := range vars {
		if variablesToOmit[v.Key] {
			continue
		}

		result = append(result, client.Variable{
			Key:    v.Key,
			Value:  v.Value,
			File:   v.File,
			Masked: v.Masked,
		})
	}
	return result
}
```

**File:** common/buildlogger/build_logger.go (L142-178)
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

**File:** common/buildlogger/innerstream/innerstream.go (L1-6)
```go
// Package innerstream parses the wire format the inner step-runner's
// timestamper emits and demuxes its content back into separate stdout and
// stderr writers. The outer step-runner re-stamps everything its builtins
// write, so without this every nested log line would carry two stacked
// timestamps.
//
```
