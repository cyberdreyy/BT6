### Title
Unsafe shell quoting of `GITLAB_ENV` path via Go `%q` in `GenerateScript` allows command injection - ([File: functions/script_legacy/internal/script_generator.go])

### Summary
`ScriptGenerator.GenerateScript` emits `export GITLAB_ENV=%q` and a companion `if [ -f %q ]... < %q` line using Go's `fmt.Sprintf`/`%q` verb to quote the `gitLabEnvFile` path, which is derived by joining `RUNNER_TEMP_PROJECT_DIR` (a job variable) with a fixed filename. Go's `%q` only guarantees a valid Go string literal, it does not escape shell metacharacters such as `$` or backtick, so a value containing `$(...)` or `` `...` `` survives into the generated bash script unescaped and is executed as command substitution when the script runs under bash.

### Finding Description
The vulnerable code path is:

- `functions/script_legacy/script_legacy.go:142-146` reads `RUNNER_TEMP_PROJECT_DIR` straight out of `builtinCtx.GetJobVars()` and does `filepath.Join(tmpDir, "gitlab_runner_env")` with no character validation or shell-sanitization: [1](#0-0) 
- That value flows into `internal.ScriptGeneratorConfig.GitLabEnvFile` and is written into the script header via Go's `%q` verb: [2](#0-1) 
- The resulting script text is handed to `internal.NewExecutor(...).Execute(ctx, script)` in `script_legacy.go:176-179`, which runs it under the detected shell (bash), i.e. the header lines are interpreted by bash exactly as generated.

Go's `%q` formatting rules escape only what is required to produce a valid Go string literal (backslash, double quote, non-printable/control runes, and non-UTF8 bytes). It does **not** escape `$`, backtick, `!`, or parentheses, because none of those are special to the Go string-literal grammar. Bash double-quoted strings, however, still perform command substitution for `$(...)` and `` `...` `` and parameter expansion for `$VAR`. Consequently, if the string handed to `%q` contains `$(cmd)` or a backtick-delimited command, the emitted script line becomes e.g.:
```
export GITLAB_ENV="/tmp/proj$(cmd)/gitlab_runner_env"
```
which bash will execute as a command substitution before the `export` even completes — this happens in the header, before any user script line runs, matching the "scoped impact" in the question.

Existing checks do not stop this: there is no validation of `RUNNER_TEMP_PROJECT_DIR`'s content against shell metacharacters anywhere in `script_legacy.go` or `script_generator.go`, and `%q` is being relied upon (incorrectly) as if it were a POSIX-shell-safe quoting function.

### Impact Explanation
If `RUNNER_TEMP_PROJECT_DIR` can be influenced to contain `$(...)` or backtick sequences, arbitrary command execution occurs inside the job's shell environment at script-header setup time — before the user's own script commands run. This is a genuine shell-injection primitive scoped to command execution within the job's own execution context (not privilege escalation across jobs), matching the impact described in the question.

### Likelihood Explanation
The finding's validity is entirely conditioned on the stated precondition: that `RUNNER_TEMP_PROJECT_DIR`'s value, as read from `builtinCtx.GetJobVars()`, can be influenced by job/pipeline-supplied content rather than being purely runner-computed from a sanitized build directory path. I was not able to trace, within the time available, the exact code that populates `GetJobVars()`/the job's variable list with `RUNNER_TEMP_PROJECT_DIR` (e.g., whether it originates solely from a runner-computed `BuildDir`/temp path that is already constrained to safe characters, or whether it can be overridden by a value that GitLab or the job payload supplies). `common/spec/variables.go` shows `RUNNER_TEMP_PROJECT_DIR` (`TempProjectDirVariableKey`) is looked up generically like any other `Variables` entry [3](#0-2) , which is consistent with it being an ordinary job variable rather than a hardcoded/sanitized runner constant, but I could not fully confirm the population path or whether GitLab/Runner reserves this key against pipeline-author override. If the precondition holds (as the question stipulates), the bug is fully reachable and deterministic on every job that has a `script_legacy`/scriptv2 step. If in fact this variable is always runner-computed from a name-restricted build path (GitLab project paths disallow `$`, backticks, parentheses), the practical exploitability is much lower and reduces to a defense-in-depth/escaping-correctness issue rather than a directly triggerable RCE.

### Recommendation
Do not rely on Go's `%q` for shell quoting. Use a proper POSIX-shell single-quote escaping helper (e.g., wrap in `'...'` and replace embedded `'` with `'\''`) for any value interpolated into the generated bash script, including `gitLabEnvFile` in `script_generator.go:50-56`. Additionally, validate/sanitize `RUNNER_TEMP_PROJECT_DIR` (and any other job-variable-derived path used to build shell script text) to reject or escape shell metacharacters before use in `script_legacy.go:142-146`.

### Proof of Concept
Go unit test in `functions/script_legacy/internal/script_generator_test.go`:
```go
func TestGenerateScript_GitLabEnvFile_ShellMetacharacterInjection(t *testing.T) {
    evilPath := `/tmp/project$(touch /tmp/pwned)/gitlab_runner_env`
    gen := NewScriptGenerator(ScriptGeneratorConfig{
        ShellPath:     "/bin/bash",
        GitLabEnvFile: evilPath,
    })
    script := gen.GenerateScript([]string{"true"})

    // Assert the generated line is NOT shell-safe: it still contains
    // an unescaped command substitution sequence.
    require.Contains(t, script, `$(touch /tmp/pwned)`)

    // Execute the header alone under bash and assert injected command ran
    // (demonstrates the exploit rather than just the string shape).
    cmd := exec.Command("bash", "-c", script)
    _ = cmd.Run()
    _, err := os.Stat("/tmp/pwned")
    require.NoError(t, err, "expected injected command substitution to execute")
}
```
Expected result (given current code): the test proves the file `/tmp/pwned` gets created, demonstrating that `%q`-quoted, attacker-influenced `RUNNER_TEMP_PROJECT_DIR` content executes as a bash command substitution during header setup — confirming the bug if the precondition (attacker control over that variable's content) holds.

### Citations

**File:** functions/script_legacy/script_legacy.go (L141-146)
```go
	var gitLabEnvFile string
	if tmpDirVar, ok := builtinCtx.GetJobVars()["RUNNER_TEMP_PROJECT_DIR"]; ok {
		if tmpDir := tmpDirVar.GetStringValue(); tmpDir != "" {
			gitLabEnvFile = filepath.Join(tmpDir, "gitlab_runner_env")
		}
	}
```

**File:** functions/script_legacy/internal/script_generator.go (L46-57)
```go
	if g.gitLabEnvFile != "" {
		// Export GITLAB_ENV so user commands can append KEY=VALUE pairs to it,
		// then source any variables written by previous stages. This mirrors
		// what AbstractShell.writeExports does for the legacy shell path.
		fmt.Fprintf(&buf, "export GITLAB_ENV=%q\n", g.gitLabEnvFile)
		fmt.Fprintf(
			&buf,
			"if [ -f %q ]; then while read -r line; do export \"$line\" || true; done < %q; fi\n\n",
			g.gitLabEnvFile,
			g.gitLabEnvFile,
		)
	}
```

**File:** common/spec/variables.go (L19-27)
```go
const TempProjectDirVariableKey = "RUNNER_TEMP_PROJECT_DIR"

// tmpFile will return a canonical temp file path by prepending the job
// variables Key with the value of `RUNNER_TEMP_PROJECT_DIR` (typically the
// build's temporary directory). The returned path must be further expanded
// by/for each shell that uses it.
func (b Variables) tmpFile(s string) string {
	return path.Join(b.Value(TempProjectDirVariableKey), s)
}
```
