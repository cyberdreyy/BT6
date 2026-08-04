### Title
Cleanup script output (CleanupExec stdout/stderr) is written to the runner's logrus log unmasked, while Prepare/Run output is masked - ([File: executors/custom/custom.go])

### Summary
`Prepare` and `Run` route `PrepareExec`/`RunExec` output through `e.BuildLogger.Stream(...)`, which passes writes through the masking wrap chain (`masker`/`tokensanitizer`/`urlsanitizer`) before reaching the job trace. `Cleanup` instead routes `CleanupExec` output through `e.BuildLogger.WithFields(...).WriterLevel(...)`, which goes straight to the `logrus.Entry` (the runner's system/process log) via `Tee.WriterLevel`, completely bypassing the `wrap()` masking chain. `RunExec`/`PrepareExec`/`CleanupExec` are the same configured driver binary and can echo the same job-controlled/masked variables to stdout/stderr, so identical secret content is masked in the job trace but unmasked in the runner log.

### Finding Description
In `executors/custom/custom.go`:
- `Prepare` (line 137-138) and `Run` (line 349-350) build `commandOutputs` using `e.BuildLogger.Stream(buildlogger.StreamExecutorLevel/StreamWorkLevel, Stdout/Stderr)`. `Logger.Stream` (`common/buildlogger/build_logger.go:90-99`) calls `l.wrap(l.base, ...)`, and `wrap` (lines 213-224) applies `tokensanitizer.New`, `urlsanitizer.New`, and `masker.New` (phrase masking) before writing to the underlying job trace (`l.base`).
- `Cleanup` (lines 379-390) instead does:
  ```go
  stdoutLogger := e.BuildLogger.WithFields(logrus.Fields{"cleanup_std": "out"})
  stderrLogger := e.BuildLogger.WithFields(logrus.Fields{"cleanup_std": "err"})
  opts.out.stdout = stdoutLogger.WriterLevel(logrus.DebugLevel)
  opts.out.stderr = stderrLogger.WriterLevel(logrus.WarnLevel)
  ```
  `WithFields`/`WriterLevel` are implemented on `internal.Tee` (`common/buildlogger/internal/tee.go:29-47`). `Tee.WriterLevel` calls `t.entry.WriterLevel(level)` directly on the `logrus.Entry` - there is no call into `Logger.wrap`, so none of the masking writers (`masker`, `tokensanitizer`, `urlsanitizer`) are ever applied to this stream. This output goes to the runner's process/system log (logrus output), not the job trace, and is written completely raw.

All three of `PrepareExec`, `RunExec`, and `CleanupExec` invoke the exact same driver binary (`e.config.PrepareExec`/`RunExec`/`CleanupExec` are all typically the same custom-executor driver in practice), and `e.prepareCommand` (lines 244-275) passes the full job environment, including `CUSTOM_ENV_*` variables built from `e.Build.GetAllVariables()`, to every invocation identically. A pipeline author can define a masked CI/CD variable (e.g. a masked variable or one referencing a masked value) and have a driver script that echoes it to stdout/stderr — this is attacker/job-author-controlled behavior fully within the documented custom executor driver contract. If the driver script (which the job author or a compromised dependency of the driver script can influence via job-controlled inputs reflected into script logic, or simply a generic driver that echoes all env for debugging on every phase) prints the same secret during `cleanup_exec`, it is masked when printed during `prepare_exec`/`run_exec` in the job trace, but appears unmasked in the runner's own log file/stdout.

### Impact Explanation
This breaks the "masking coverage must be uniform across executor lifecycle hooks" invariant: secret values that GitLab guarantees will be masked in the job trace end up unmasked in the runner host's own logrus output (which is typically written to disk/journal and often collected as part of runner audit/system logs, potentially with broader read access, log aggregation, or retention than the job trace). This is a concrete, scoped leak of secret content from the job into a lower-trust-boundary artifact (runner system log) that GitLab's masking documentation does not carve out an exception for.

### Likelihood Explanation
- Preconditions: a `custom` executor configuration with `cleanup_exec` configured (common; many custom-executor drivers implement cleanup) and a job defining a masked variable that the driver reflects to stdout/stderr during cleanup (e.g., generic debug-echo behavior, or logic that varies by phase argument but still touches the same env).
- The job author fully controls which CI/CD variables are set/masked and, if the driver script logic is influenced by job-controlled values (e.g. `CI_JOB_NAME`, custom variables consumed in driver logic), can increase the likelihood the secret gets printed on cleanup specifically.
- No additional privilege is needed beyond defining pipeline variables — this is exactly the "normal GitLab user or pipeline author" threat model.
- The bug is deterministic and 100% repeatable given the driver reflects the variable during cleanup; no timing, race, or admin action required.

### Recommendation
Route `CleanupExec`'s stdout/stderr through the same masked stream path used by `PrepareExec`/`RunExec` (i.e., `e.BuildLogger.Stream(buildlogger.StreamExecutorLevel, Stdout/Stderr)` or an equivalent masked writer), rather than through `WithFields(...).WriterLevel(...)` which bypasses `wrap()`. If writing cleanup output to the runner's own log is intentionally desired for debugging, mask it first (e.g., wrap the writer with `masker.New`/`tokensanitizer.New`/`urlsanitizer.New` using the same phrase/prefix lists as `Logger.wrap`) before handing it to `WriterLevel`.

### Proof of Concept
Go differential unit test in `executors/custom/custom_test.go`:
1. Configure a fake `command.Command`/driver stub (as already used in existing tests via `commandFactory`) so that `PrepareExec`, `RunExec`, and `CleanupExec` all execute a script that writes a known masked-variable value (e.g. set via `e.Build.GetAllVariables()` with a variable flagged for masking, matching `BuildLogger`'s `MaskPhrases`) to stdout.
2. Construct the executor with a `BuildLogger` configured with `MaskPhrases: []string{"supersecretvalue"}` and a fake job `Trace` capturing job-trace output, plus a `logrus.Entry` with a hook/writer capturing runner-log output.
3. Call `executor.Prepare(...)` and `executor.Run(...)`; assert the captured job trace contains `[MASKED]` and does **not** contain `supersecretvalue`.
4. Call `executor.Cleanup()`; assert the captured runner-log output (from the logrus hook attached to the entry used by `BuildLogger`) still contains the literal `supersecretvalue` (not masked), demonstrating the inconsistency.
5. Assertion failure criterion for the fix: after remediation, the Cleanup-path capture should also contain `[MASKED]` and not the raw secret.