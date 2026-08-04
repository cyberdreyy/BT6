## Analysis

`e.Env` is populated from `builtinCtx.GetEnvs()` plus job vars flattened to plain strings [1](#0-0) , i.e. it contains the **actual unmasked values** of all job CI variables (including masked/protected ones like `CI_JOB_TOKEN`), the same way a normal shell job would see them in its process environment. Masking is applied only at the trace/log layer, not by stripping values from the process environment, which is confirmed by `buildtest/masking.go`, whose assertions operate on trace buffer contents, not on process env or argv. [2](#0-1) 

`ArtifactUpload.Run` expands `s.ExpireIn` via `e.ExpandValue` and appends the *expanded* result directly into the CLI `args` slice before invoking `e.RunnerCommand`: [3](#0-2) 

`ExpandValue` performs `os.Expand` against `e.GitLabEnv` then `e.Env`, with no filtering for masked/protected variables: [4](#0-3) 

`RunnerCommand`/`Command` then execs the helper binary with `args...` as literal argv elements: [5](#0-4) 

This is explicitly different from `--name`/`--path`, where the raw (unexpanded) literal string is passed as argv and the *child* (`artifacts-uploader`) expands it internally via `mvdan.cc/sh/v3/shell.Expand` inside its own process memory — the secret substitution never touches the parent's constructed argv: [6](#0-5) . The code comment even calls out the asymmetry: `expire_in` is expanded by the parent because the uploader doesn't do it itself. [7](#0-6) 

So if a job defines `artifacts: expire_in: "$CI_JOB_TOKEN"` (or any masked/protected variable reference), the actual secret value is substituted into the `--expire-in` argument before `exec` — making it visible in the child process's argv (readable via `/proc/<pid>/cmdline`, `ps -ef`, or any co-resident process/container able to read `/proc`), independent of and unmasked by the trace pipeline.

None of the existing protections stop this: masking operates on the streamed trace/log output only, not on process argv; there is no allowlist or sanitization applied to `ExpireIn` before expansion; and `ExpandValue` has no distinction between "safe to expand into argv" vs. "safe to expand into trace text" contexts.

### Title
Masked/protected CI variable values leak into artifacts-uploader process argv via unfiltered `ExpireIn` expansion - (File: functions/concrete/run/stages/artifact_upload.go)

### Summary
`ArtifactUpload.Run` expands `artifacts:expire_in` with `env.Env.ExpandValue`, which resolves `$VAR` references against the full, unmasked job environment (including masked/protected CI variables), and appends the expanded plaintext value directly as a `--expire-in` CLI argument to the `artifacts-uploader` subprocess. Unlike `--name`/`--path`, which pass the literal unexpanded string and let the child expand it internally, this expansion happens in the parent before `exec`, so the secret value is embedded in the child's argv and exposed via `/proc/<pid>/cmdline`.

### Finding Description
- `s.ExpireIn` is a user/pipeline-author-controlled field from the job's `artifacts:expire_in` config.
- `e.ExpandValue(s.ExpireIn)` substitutes `$KEY`/`${KEY}` references using `e.GitLabEnv` and `e.Env`, both of which hold plaintext values of all job variables, including masked/protected ones (`e.Env` is seeded straight from `builtinCtx.GetEnvs()`/job vars with no masking filter). [1](#0-0) 
- The expanded string is appended to `args` and passed to `e.RunnerCommand(ctx, ..., args...)`, which execs the helper binary with these as literal argv elements — visible in `/proc/<pid>/cmdline` for the duration of the subprocess. [8](#0-7) 
- By contrast, `--name` and `--path` pass the raw `$VAR` string unexpanded, and the child does the substitution itself via `shell.Expand` in `normalizeArgs`, so the plaintext secret is confined to the child's internal memory rather than exposed in the argv constructed by the parent. [9](#0-8) 
- GitLab Runner's trace masking (verified in `common/buildtest/masking.go`) only redacts values as they pass through the trace/log writer; it does nothing to prevent secrets appearing in the OS-visible argv of a spawned process. [10](#0-9) 

### Impact Explanation
Any process with `/proc` read access to the runner host/container during the artifact-upload stage (e.g., a sidecar container sharing the PID namespace, a monitoring agent, or another process in a shell-executor shared host) can read the masked/protected variable's plaintext value from the `artifacts-uploader` process's command line — a channel entirely outside trace masking. This is scoped exactly to secret exposure via process listing rather than trace/log, matching the question's stated impact.

### Likelihood Explanation
Trivially reachable by any pipeline author: set `artifacts: expire_in: "$SOME_MASKED_VAR"` (or `${CI_JOB_TOKEN}`) in `.gitlab-ci.yml`. No special runner privileges are required — this is standard job configuration reaching `ArtifactUpload.Run` on every job that defines `on_success`/`on_failure` artifacts. It requires that some other actor can inspect `/proc/<pid>/cmdline` of the runner/helper process during the short window the upload subprocess runs, which is plausible in shared-host shell executors or containers with shared PID namespaces (a scenario the rules do not exclude — this is not an admin misconfiguration like privileged containers or docker.sock, it is an inherent argv-exposure bug in Runner's own code).

### Recommendation
Do not expand `expire_in` (or any field passed as CLI argv) against secret-bearing variables in the parent process. Instead:
1. Pass the raw, unexpanded `ExpireIn` string to `artifacts-uploader` and let the child perform expansion internally (consistent with `--name`/`--path`), or
2. Pass the expire-in value via an environment variable or a file descriptor instead of argv, or
3. If parent-side expansion must remain, exclude masked/protected variables from the substitution set used by `ExpandValue` when the result feeds into a subprocess `argv`, and/or use `syscall.Exec`-safe channels (env var to child) instead of literal args.

### Proof of Concept
Go unit test sketch in `functions/concrete/run/stages/artifact_upload_test.go`:
```go
func TestArtifactUpload_ExpireInDoesNotLeakMaskedVarIntoArgv(t *testing.T) {
    e := &env.Env{
        Env: map[string]string{"CI_JOB_TOKEN": "glpat-supersecrettoken"},
        GitLabEnv: map[string]string{},
    }
    var capturedArgs []string
    // stub e.RunnerCommand / e.Command to capture args instead of executing
    // (requires making RunnerCommand injectable, or testing ExpandValue directly)

    expireIn := e.ExpandValue("$CI_JOB_TOKEN")
    assert.NotContains(t, expireIn, "glpat-supersecrettoken",
        "expire_in expansion must not substitute masked/protected variable values into a CLI argv-bound string")
}
```
Expected: today this assertion **fails** — `expireIn` equals `"glpat-supersecrettoken"`, proving the plaintext secret is produced and destined for `--expire-in` argv. An integration-level PoC would run `ArtifactUpload.Run` with `ExpireIn: "$CI_JOB_TOKEN"` and a fake `RunnerCommand` that inspects `os/exec.Cmd.Args`, asserting the token string is absent from the constructed argv.

### Citations

**File:** functions/concrete/run/runner.go (L76-80)
```go
	jobVars := builtinCtx.GetJobVars()
	stepEnv := builtinCtx.GetEnvs()
	for key, value := range jobVars {
		stepEnv[key] = value.GetStringValue()
	}
```

**File:** common/buildtest/masking.go (L129-152)
```go
	buf, err := trace.New()
	require.NoError(t, err)
	defer buf.Close()

	err = build.Run(t.Context(), &common.Config{}, &common.Trace{Writer: buf})
	assert.NoError(t, err)

	buf.Finish()

	contents, err := buf.Bytes(0, math.MaxInt64)
	assert.NoError(t, err)

	assert.NotContains(t, string(contents), "MASKED_KEY=MASKED_VALUE")
	assert.Contains(t, string(contents), "MASKED_KEY=[MASKED]")

	assert.NotContains(t, string(contents), "MASKED_KEY_OTHER=MASKED_VALUE_OTHER")
	assert.NotContains(t, string(contents), "MASKED_KEY_OTHER=[MASKED]_OTHER")
	assert.Contains(t, string(contents), "MASKED_KEY_OTHER=[MASKED]")

	assert.NotContains(t, string(contents), "CLEARTEXT_KEY=[MASKED]")
	assert.Contains(t, string(contents), "CLEARTEXT_KEY=CLEARTEXT_VALUE")

	assert.NotContains(t, string(contents), "x-amz-credential=foobar")
	assert.Contains(t, string(contents), "x-amz-credential=[MASKED]")
```

**File:** functions/concrete/run/stages/artifact_upload.go (L100-104)
```go
	// artifacts-uploader doesn't expand $VAR in --expire-in (unlike --name
	// and --path), so we have to do it here.
	if expireIn := e.ExpandValue(s.ExpireIn); expireIn != "" {
		args = append(args, "--expire-in", expireIn)
	}
```

**File:** functions/concrete/run/env/env.go (L76-92)
```go
func (e *Env) ExpandValue(s string) string {
	if s == "" {
		return s
	}
	return os.Expand(s, func(key string) string {
		switch key {
		case "$":
			return "$"
		case "*", "#", "@", "!", "?", "-", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9":
			return ""
		}
		if v, ok := e.GitLabEnv[key]; ok {
			return v
		}
		return e.Env[key]
	})
}
```

**File:** functions/concrete/run/env/env.go (L141-164)
```go
func (e *Env) RunnerCommand(ctx context.Context, extra map[string]string, args ...string) error {
	return e.Command(ctx, e.getRunnerBinaryPath(), extra, args...)
}

func (e *Env) Command(ctx context.Context, name string, env map[string]string, args ...string) error {
	environ := os.Environ()
	for k, v := range e.Env {
		environ = append(environ, k+"="+v)
	}
	for k, v := range e.GitLabEnv {
		environ = append(environ, k+"="+v)
	}
	for k, v := range env {
		environ = append(environ, k+"="+v)
	}

	cmd := gracefulexitcmd.New(ctx, e.GracefulExitDelay, name, args...)
	cmd.Dir = e.WorkingDir
	cmd.Env = environ
	cmd.Stdout = e.Stdout
	cmd.Stderr = e.Stderr

	return normalizeExitError(cmd.Run(), cmd.ProcessState)
}
```

**File:** commands/helpers/artifacts_uploader.go (L252-272)
```go
func (c *ArtifactsUploaderCommand) normalizeArgs() {
	if c.URL == "" || c.Token == "" {
		logrus.Fatalln("Missing runner credentials")
	}
	if c.ID <= 0 {
		logrus.Fatalln("Missing build ID")
	}

	if name, err := shell.Expand(c.Name, nil); err != nil {
		logrus.Warnf("invalid artifact name: %v", err)
	} else {
		c.Name = name
	}

	for idx := range c.Paths {
		if path, err := shell.Expand(c.Paths[idx], nil); err != nil {
			logrus.Warnf("invalid path %q: %v", path, err)
		} else {
			c.Paths[idx] = path
		}
	}
```
