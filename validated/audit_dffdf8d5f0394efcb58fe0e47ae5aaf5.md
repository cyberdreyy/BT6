### Title
`logUsedImages` writes fully-expanded `Image.Name`/`Service.Name` values (which may embed masked CI/CD variable values) to the runner's logrus system log, bypassing the buildlogger mask chain - (File: common/build.go)

### Summary
`Build.logUsedImages` (`common/build.go:1651`) logs `b.Job.Image.Name` and each `b.Job.Services[].Name` via `b.Log().WithFields(...).Info(...)`, which is a raw `logrus.Entry`, not the masked `buildlogger.Logger` used for the job trace. Because `logUsedImages` runs after `b.expandContainerOptions()` in `Build.Run` (`common/build.go:1554-1555`), any CI/CD variable reference embedded in the image/service name (including masked/protected variables) is expanded to its real value before being written to this unmasked log sink.

### Finding Description
In `Build.Run`, the call order is: [1](#0-0) 
`resolveSecrets` → `expandContainerOptions` → `logUsedImages` → `getNewLogger` (which is where the buildlogger mask list, `b.GetAllVariables().Masked()`, is actually built and applied, at `common/build.go:1637-1648`).

`logUsedImages` itself is gated only by the `LogImagesConfiguredForJob` feature flag and does no masking: [2](#0-1) 

The repo's own test `Test_logUsedImages_expandsVariablesInBuildRun` (`common/build_test.go:3691-3730`) confirms the exploitable behavior end-to-end: `Image.Name` is set to `"$JOB_IMAGE"` and `Services[0].Name` to `"$SERVICE_IMAGE"`, and after `build.Run` executes, the logrus hook captures the *expanded* values (`registry.example.com/job:latest`, `registry.example.com/service:v1`) in the `image_name` field — proving that variable expansion happens before this log line and that no masking is applied to it, unlike the job trace path which goes through `buildlogger.New(... MaskPhrases: b.GetAllVariables().Masked() ...)`.

If a pipeline author (attacker, in the "unprivileged pipeline author" threat model) sets `image: "registry.example.com/repo:$SOME_MASKED_VARIABLE"` (or embeds a masked variable in a service name), the expanded, unmasked secret value is written into the runner process's own logrus output (`b.Log()`), which is a distinct sink from the job trace that buildlogger normally protects. This is a genuine mask-chain bypass because the code path intentionally exists to interpolate variables into image references (this is standard, documented GitLab CI functionality — `image: $MY_IMAGE_VAR` is a supported pattern), but the masking system was only wired into the trace writer, not into this diagnostic log line.

### Impact Explanation
The scoped impact is that a masked/protected CI/CD variable value can be exfiltrated into the runner's local system/debug log stream, which is a separate audience from the job trace viewers that GitLab's masking feature is designed to protect against. In many deployments, runner system logs are shipped to centralized logging/monitoring infrastructure with broader access than the specific project's job traces (e.g., shared-runner admins, log aggregation platforms, SIEM tooling) — so this creates a path where a value the masking feature was meant to keep out of visible output ends up in a logging system outside the trace's access control, even though the trace itself would have shown it masked. This is scoped narrowly to whatever variable value the pipeline author chooses to place into `image:`/`services[].image` fields, so it is not an arbitrary-secret-exfiltration primitive, but it does defeat masking for that specific value.

### Likelihood Explanation
- Precondition: `FF_LOG_IMAGES_CONFIGURED_FOR_JOB` must be enabled (default `false`, per `helpers/featureflags/flags.go:330-335`), limiting exposure to runners that have opted into this flag.
- Attacker only needs the ability to author `.gitlab-ci.yml` (or a pipeline that sets `image`/`services`) and reference a masked variable in the image/service name field — both fully within a normal pipeline author's control.
- The behavior is deterministic and reproducible (confirmed by the existing repo test), not a race condition or edge case.

### Recommendation
Route `logUsedImages`'s output through masking before logging, e.g., apply `b.GetAllVariables().Masked()` phrase substitution (or reuse `buildlogger`'s mask function) to `i.Name` and `i.ExecutorOptions.Docker.Platform` before adding them as logrus fields, or move `logUsedImages` to log via `b.logger` (the masked buildlogger) instead of `b.Log()`, ensuring parity with how the job trace protects the same data.

### Proof of Concept
Extend the existing `Test_logUsedImages_expandsVariablesInBuildRun` (`common/build_test.go:3691`) pattern:
1. Enable `LogImagesConfiguredForJob`.
2. Add a masked variable, e.g. `spec.Variable{Key: "SECRET_TOKEN", Value: "s3cr3t-value", Masked: true, Public: true}`.
3. Set `build.Image = spec.Image{Name: "registry.example.com/repo:$SECRET_TOKEN"}`.
4. Run `build.Run(...)`.
5. Assert that the trace/writer output does **not** contain `"s3cr3t-value"` (masked correctly), but assert that the logrus hook's captured `image_name` field **does** contain `"s3cr3t-value"` in plaintext — demonstrating the mask-chain bypass into the runner's own log sink.

### Citations

**File:** common/build.go (L1549-1557)
```go
	err = b.resolveSecrets(trace)
	if err != nil {
		return wrapSecretResolvingError(err)
	}

	b.expandContainerOptions()
	b.logUsedImages()

	b.logger = b.getNewLogger(trace, b.Log(), false)
```

**File:** common/build.go (L1651-1679)
```go
func (b *Build) logUsedImages() {
	if !b.IsFeatureFlagOn(featureflags.LogImagesConfiguredForJob) {
		return
	}

	fields := func(i spec.Image) logrus.Fields {
		if i.Name == "" {
			return nil
		}

		fields := logrus.Fields{
			"image_name": i.Name,
		}
		if i.ExecutorOptions.Docker.Platform != "" {
			fields["image_platform"] = i.ExecutorOptions.Docker.Platform
		}

		return fields
	}

	imageFields := fields(b.Job.Image)
	if imageFields != nil {
		b.Log().WithFields(imageFields).Info("Image configured for job")
	}

	for _, service := range b.Job.Services {
		b.Log().WithFields(fields(service)).Info("Service image configured for job")
	}
}
```
