### Title
Service-level Masked/File variables are serialized into `CUSTOM_ENV_CI_JOB_SERVICES` without masking - (File: executors/custom/custom.go)

### Summary
`getCIJobServicesEnv` in `executors/custom/custom.go` builds `CI_JOB_SERVICES` by JSON-marshaling each service's `Variables` map with plain resolved values, regardless of the `Masked` or `File` flag on the source `spec.Variable`. This value is set verbatim as `CUSTOM_ENV_CI_JOB_SERVICES` in the custom executor's child process environment, and the codebase's own test `TestExecutor_ServicesEnv` ("exposes masked variable value in plain text") documents that a `Masked: true` service variable (`REDIS_PASSWORD=supersecret`) ends up unmasked in that JSON blob.

### Finding Description
In `executors/custom/custom.go` the `prepareCommand` function calls `e.getCIJobServicesEnv()` and appends its result to the variable set used to populate `CUSTOM_ENV_*` entries in the spawned driver process's environment: [1](#0-0) 

`getCIJobServicesEnv` serializes `e.Build.Services` (including each service's `Variables`) to JSON to build the `CI_JOB_SERVICES` variable value. As shown in the test `TestExecutor_ServicesEnv`, a service variable defined with `Masked: true` (e.g. `REDIS_PASSWORD=supersecret`) is serialized directly into the `jsonService.Variables` map with its plaintext value, with no masking or redaction step applied: [2](#0-1) 

Separately, `File: true` service variables are also resolved to their raw content and placed in the same map, as shown in the "resolves file-type service variable to its contents" test case: [3](#0-2) 

The trace/log masking system (`common/buildlogger`) only masks phrases derived from `b.GetAllVariables().Masked()`, which collects `Masked: true` values from the flat `Variables` list via `spec.Variables.Masked()`: [4](#0-3) [5](#0-4) 

Service definitions (`Build.Services`) are a structurally separate field from `Build.Variables`, so `Masked()`-derived mask phrases are populated only from top-level job/build variables. There is no equivalent mechanism that walks `Build.Services[*].Variables` to register their `Masked: true` values as trace mask phrases before `CUSTOM_ENV_CI_JOB_SERVICES` is emitted. This means the masked secret both (a) appears in plaintext in the custom-executor driver process environment via `CUSTOM_ENV_CI_JOB_SERVICES`, and (b) is not registered for masking, so if any driver script or job step echoes that variable to trace, the plaintext secret is written unmasked to the job log.

### Impact Explanation
A pipeline author who defines a service with a `Masked` (or `File`) type variable can have that secret value appear in plaintext in the custom executor's `CUSTOM_ENV_CI_JOB_SERVICES` environment variable, which the driver script (`config_exec`/`run_exec`/`prepare_exec`, all attacker-influenced or attacker-adjacent scripts in shared-runner "bring your own executor" scenarios) can read or re-echo. Because the value is not part of the masking phrase list, any accidental or intentional echo of `CUSTOM_ENV_CI_JOB_SERVICES` in the job trace bypasses GitLab's masking guarantee, exposing what should be a protected secret in the job log, violating the invariant that masked/protected values must never leave masking protection.

### Likelihood Explanation
This requires the custom executor and a job author with the ability to declare `services` with `variables` (a normal CI feature, not privileged). It is deterministically reproducible: any masked service variable is unconditionally serialized in plaintext by `getCIJobServicesEnv`, as confirmed by the existing `TestExecutor_ServicesEnv` "exposes masked variable value in plain text" test case, which asserts the plaintext value flows through without any masking transformation.

### Recommendation
Either (a) exclude `Masked`/`File` service variables' actual values from the `CI_JOB_SERVICES` JSON payload (e.g., emit a placeholder or omit sensitive variables from this auxiliary env var), or (b) register all `Masked: true` values found in `Build.Services[*].Variables` into the `BuildLogger`'s mask-phrase list (alongside `b.GetAllVariables().Masked()`) before the trace is written, so that any echo of `CUSTOM_ENV_CI_JOB_SERVICES` is masked consistently with other secret variables.

### Proof of Concept
Extend `TestExecutor_ServicesEnv` in `executors/custom/custom_test.go`:
1. Define a service with `Variables: spec.Variables{{Key: "REDIS_PASSWORD", Value: "supersecret", Masked: true}}` (already present as the "exposes masked variable value in plain text" case).
2. Assert `CUSTOM_ENV_CI_JOB_SERVICES` contains the literal string `supersecret` (already implicitly proven, since `assertEnvValue` expects the plaintext value in the JSON).
3. Additionally, construct a `buildlogger.Logger` via `b.getNewLogger` and confirm `"supersecret"` is absent from `MaskPhrases` (i.e., not present in `b.GetAllVariables().Masked()`), and demonstrate that writing `CUSTOM_ENV_CI_JOB_SERVICES=...supersecret...` through the logger does not produce `[MASKED]` in trace output — proving the masking registry does not cover this env var.

### Citations

**File:** executors/custom/custom.go (L259-267)
```go
	// Append job_env defined variable first to avoid overwriting any CI/CD or predefined variables.
	for k, v := range e.jobEnv {
		cmdOpts.Env = append(cmdOpts.Env, fmt.Sprintf("%s=%s", k, v))
	}

	variables := append(e.Build.GetAllVariables(), e.getCIJobServicesEnv())
	for _, variable := range variables {
		cmdOpts.Env = append(cmdOpts.Env, fmt.Sprintf("CUSTOM_ENV_%s=%s", variable.Key, variable.Value))
	}
```

**File:** executors/custom/custom_test.go (L1161-1180)
```go
		"exposes masked variable value in plain text": {
			config: runnerConfig,
			adjustExecutor: adjustExecutorServices(spec.Services{
				{
					Name: "redis:latest",
					Variables: spec.Variables{
						{Key: "REDIS_PASSWORD", Value: "supersecret", Masked: true},
					},
				},
			}),
			assertCommandFactory: assertEnvValue(
				[]jsonService{
					{
						Name:      "redis:latest",
						Alias:     "",
						Variables: map[string]string{"REDIS_PASSWORD": "supersecret"},
					},
				},
			),
		},
```

**File:** executors/custom/custom_test.go (L1181-1200)
```go
		"resolves file-type service variable to its contents": {
			config: runnerConfig,
			adjustExecutor: adjustExecutorServices(spec.Services{
				{
					Name: "postgres:latest",
					Variables: spec.Variables{
						{Key: "DB_PASSWORD", Value: "secret-file-content", File: true},
					},
				},
			}),
			assertCommandFactory: assertEnvValue(
				[]jsonService{
					{
						Name:      "postgres:latest",
						Alias:     "",
						Variables: map[string]string{"DB_PASSWORD": "secret-file-content"},
					},
				},
			),
		},
```

**File:** common/build.go (L1637-1649)
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
}
```

**File:** common/spec/variables.go (L169-176)
```go
func (b Variables) Masked() (masked []string) {
	for _, variable := range b {
		if variable.Masked {
			masked = append(masked, variable.Value)
		}
	}
	return
}
```
