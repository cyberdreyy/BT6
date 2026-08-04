### Title
Masked variable values can leak unmasked via `os.Chmod` error path logged by `CacheInitCommand.Execute` - (File: commands/helpers/cache_init.go)

### Summary
`CacheInitCommand.Execute` calls `os.Chmod(path, os.ModePerm)` on attacker-influenced cache paths and, on failure, logs the raw Go error with `logrus.WithError(err).Error("failed to chmod path")`. Because `os.Chmod` failures return a `*fs.PathError` whose `Error()` string embeds the full failing path, and this path can be derived from a job's `cache:paths`/`cache:key` (which may itself be expanded from a `Masked:true` CI variable via `build.GetAllVariables().ExpandValue`), the secret value can be echoed verbatim into the helper process's own log output, which is not routed through the buildlogger masking pipeline (`masker`/`tokensanitizer`/`urlsanitizer`).

### Finding Description
`CacheInitCommand.Execute` (`commands/helpers/cache_init.go:22-33`) iterates over CLI args and does:
```go
err := os.Chmod(path, os.ModePerm)
if err != nil {
    logrus.WithError(err).Error("failed to chmod path")
}
```
`path` here is one of the `ctx.Args()` passed to the `gitlab-runner-helper cache-init` subcommand. These paths originate from cache configuration (`cache:paths`, `cache:key`) resolved on the job side (`shells/abstract.go` cache handling, `cache/cachekey`), which supports variable expansion of job-defined CI variables, including masked ones, before being turned into filesystem paths passed to the helper binary.

The critical issue is that `logrus.WithError(err).Error(...)` in this command operates on the **helper process's own logrus logger**, which writes directly to the helper's stdout/stderr. This is a separate execution context from the main runner's job trace, which is the only place the masking pipeline (`masker`, `tokensanitizer`, `urlsanitizer`) is applied to redact known masked variable values. The helper process is not given the list of masked values, so nothing it logs is ever sanitized.

If an attacker crafts a cache path (via a masked variable embedded in `cache:key` or `cache:paths`) such that `os.Chmod` fails on it (e.g., a non-existent parent directory, permission-denied path, or a symlink loop), the resulting `*fs.PathError.Error()` string — which includes the full path text, and therefore the secret substring — is printed unmasked by the helper's logrus output.

Whether this reaches the visible job log depends on how the calling executor captures/forwards the helper's stdout/stderr into the job trace. For executors that pipe the helper container/process output back into the job's log stream (e.g., Kubernetes/Docker helper container logs, or shell executor subprocess capture), this text becomes visible outside the masking-protected trace writer, violating the "masked values must never appear unmasked in any runner-produced output" invariant.

### Impact Explanation
A pipeline author who can set a masked CI variable and use it in `cache:key`/`cache:paths` can force a chmod failure on a path containing that value, causing the secret to be printed in the raw helper log output, unprotected by masking. If that helper output is captured into the job log or any log aggregation, this results in secret disclosure to whoever can view the job log/artifacts of that pipeline (self-disclosure in the common case, but a real breach of the runner's masking guarantee, and cross-project impact wherever this masked value is shared, e.g., group/instance-level variables).

### Likelihood Explanation
Feasible: the mechanism only requires an attacker to (1) reference a masked variable in `cache:paths` or `cache:key`, and (2) construct a value/paths structure that causes `os.Chmod` to fail (invalid path segments, permission issues, or a deliberately broken symlink target under attacker control within the build directory). Both are achievable by a normal pipeline author with control over `.gitlab-ci.yml` and CI variables. Reproducible deterministically once the chmod-failure condition is set up.

### Recommendation
- Never log the raw `error` value (or any string derived from user/job-controlled paths) directly via logrus in helper commands; log only sanitized/generic messages, or explicitly scrub known-sensitive substrings before logging.
- If the helper process needs diagnostic output, propagate the runner's masking value list (or a scrubbing function) to the helper subcommand, or route the helper's output through the same masking writer used for job traces before it is persisted/forwarded.
- Alternatively, avoid embedding raw expanded secret values into filesystem paths used for cache directories; use hashed/normalized directory names (the cache key hashing mechanism already used elsewhere) rather than the raw expanded value.

### Proof of Concept
Go unit test sketch for `commands/helpers/cache_init_test.go`:
```go
func TestCacheInitCommand_DoesNotLeakMaskedValueOnChmodFailure(t *testing.T) {
    secret := "s3cr3t-M4sk3dValue"
    // construct a path guaranteed to fail chmod, e.g. containing the secret
    // in a component that does not exist / triggers ENOENT or a symlink loop
    badPath := filepath.Join(t.TempDir(), secret, "nonexistent", "loop")

    var buf bytes.Buffer
    logrus.SetOutput(&buf)
    defer logrus.SetOutput(os.Stderr)

    app := cli.NewApp()
    app.Commands = []cli.Command{NewCacheInitCommand()}
    err := app.Run([]string{"cache-init", "cache-init", badPath})
    require.NoError(t, err) // Execute doesn't return error, logs it

    assert.NotContains(t, buf.String(), secret,
        "masked secret value leaked in unmasked helper log output")
}
```
Expected (current) result: assertion fails because `buf.String()` contains `secret` inside the logged `PathError` text, proving the leak. Fix should make the assertion pass by ensuring the log message no longer contains the raw path/secret.