### Title
Job-controlled cache key breaks out of its string context via unescaped `$`/backtick in the `CACHE_METADATA` heredoc, enabling command injection during cache archiving - ([File: shells/bash.go], [File: helpers/shell_escape.go])

### Summary
The specific hypothesis about `s3CredentialsAdapter.GetCredentials` does not hold: that function only returns statically-configured AWS keys from `cacheconfig.CacheS3Config` [1](#0-0) , with no attacker-controlled cache-key data anywhere near it. However, the underlying invariant the question is actually probing — that a job-controlled cache key (`spec.Cache.Key`, expanded and stored in `cacheConfig.HumanKey`) must never escape its string-value context in a generated script — is genuinely violated, but in a different function than cited: `BashWriter.DotEnvVariables` combined with `helpers.DotEnvEscape`.

### Finding Description
`cacheConfig.HumanKey` is derived from the job's `cache.key` via `variables.ExpandValue` in `newCacheConfig` [2](#0-1) . It is placed into `metadata["cachekey"]` and JSON-marshalled into `env["CACHE_METADATA"]` in `addCacheUploadCommand` [3](#0-2) . This env map is written to a temp file via `writeCacheExports` → `w.DotEnvVariables` [4](#0-3) .

For bash, `DotEnvVariables` emits the file contents inside an **unquoted heredoc delimiter** (`cat << EOF ... EOF`): [5](#0-4) 
Because the delimiter `EOF` is not quoted (`<< 'EOF'`), bash performs the same expansions as inside double quotes on the heredoc body — i.e. `$(...)`, backtick command substitution, and `$VAR` expansion are all live.

The value is escaped by `DotEnvEscape`, which only escapes backslash, double-quote, `\n`, and `\r`: [6](#0-5) 
It does **not** escape `$` or `` ` ``. Consequently, if `HumanKey` contains `$(id)` or `` `id` ``, that sequence is written verbatim (inside the double-quoted-looking `KEY="..."` line) into the heredoc body, and when the generated shell script runs `cat << EOF > file`, bash executes the injected command substitution while expanding the heredoc, before the resulting text is written to the cache-metadata env file.

This differs from the `Command`/`IfCmdWithOutput` argument path, which is safe: those use `BashWriter.escape` → `helpers.ShellEscape`/`PosixShellEscape`, both of which do escape `$` and backticks (verified by `TestBash_CommandShellEscapes`) [7](#0-6) . The unsafe path is specifically the heredoc-based `DotEnvVariables` mechanism, which is a separate quoting scheme from `escape()` and was not covered by the same escaping guarantees.

The PowerShell equivalent uses an interpolated here-string (`@"…"@`), which similarly performs `$`-based interpolation and is not neutralized by `DotEnvEscape` either [8](#0-7) .

### Impact Explanation
The injected command executes inside the generated build-stage script at the point the `CACHE_METADATA`/cache-credentials env file is being materialized (during the archive/restore-cache stage), in whatever container/context runs that stage. In executors where the predefined cache stage runs in the *same* build container as the user's own `script:` (Docker/shell executors), this doesn't grant a new privilege boundary crossing since the job already has full command execution there. However, in executors where predefined stages execute in a separate "helper" container/context (e.g. Kubernetes executor's helper container) that may have different capabilities, mounts, or credentials than the build container, this provides an unintended code-execution point outside the user's normal script execution context, and could expose or misuse GoCloud/S3 STS credentials generated specifically for the cache upload (which are meant to be scoped/ephemeral, written into the same env file) rather than exposed to the user's own script.

### Likelihood Explanation
Fully attacker-reachable with a single `.gitlab-ci.yml` field: `cache: key: '$(id)'` or a CI variable expanding to a payload containing `$(...)`/backticks, requires no special runner configuration beyond having `cache:` configured with paths/untracked set (so the archive stage actually runs), and reproduces deterministically every time the archive-cache build stage is generated.

### Recommendation
- In `helpers.DotEnvEscape` (`helpers/shell_escape.go`), also escape `$` and `` ` `` in values, or better, avoid embedding the escaped value inside a shell-expandable heredoc.
- In `BashWriter.DotEnvVariables` (`shells/bash.go`), quote the heredoc delimiter (`<< 'EOF'`) so the body is treated as a literal, unexpanded block, eliminating the entire class of injection regardless of value content.
- Apply the analogous fix to `PsWriter.DotEnvVariables` (avoid PowerShell-interpolated here-strings for untrusted content, or escape `$`).

### Proof of Concept
```go
// shells/bash_test.go
func TestBashWriter_DotEnvVariables_CommandSubstitutionNotExecutable(t *testing.T) {
    w := BashWriter{TemporaryPath: "foo/bar"}
    w.DotEnvVariables("test", map[string]string{
        "CACHE_METADATA": `{"cachekey":"$(id)"}`,
    })
    script := w.String()
    // Assert the literal payload appears unescaped (demonstrates the bug):
    assert.Contains(t, script, `$(id)`)
    // Fixed version should assert the heredoc delimiter is quoted, e.g.:
    // assert.Contains(t, script, "cat << 'EOF'")
}
```
Integration-level PoC: run `shellstest.OnEachShellWithWriter` with `w.DotEnvVariables("cache_env", map[string]string{"CACHE_METADATA": `{"cachekey":"$(touch /tmp/pwned)"}`})`, execute the resulting script via `runShell`, and assert `/tmp/pwned` was NOT created after fix (currently it would be, since `cat << EOF` expands `$(...)` during heredoc processing).

### Citations

**File:** cache/s3/credentials_adapter.go (L14-26)
```go
func (a *s3CredentialsAdapter) GetCredentials() map[string]string {
	credMap := make(map[string]string)

	// For IAM instance profiles, Go Cloud will fetch the credentials with the AWS SDK.
	if a.config.AccessKey == "" || a.config.SecretKey == "" {
		return credMap
	}

	credMap["AWS_ACCESS_KEY_ID"] = a.config.AccessKey
	credMap["AWS_SECRET_ACCESS_KEY"] = a.config.SecretKey

	return credMap
}
```

**File:** shells/abstract.go (L138-149)
```go
	rawKey := path.Join("/", build.JobInfo.Name, build.GitInfo.Ref)[1:]
	if userKey != "" {
		rawKey = build.GetAllVariables().ExpandValue(userKey)
	}

	hasher := func(s string) string { return s }
	sanitizer := cachekey.Sanitize
	// if hash key support is enabled, we don't need to sanitize keys anymore
	if build.IsFeatureFlagOn(featureflags.HashCacheKeys) {
		hasher = func(s string) string { return fmt.Sprintf("%x", sha256.Sum256([]byte(s))) }
		sanitizer = func(s string) (string, error) { return s, nil }
	}
```

**File:** shells/abstract.go (L616-618)
```go
func (b *AbstractShell) writeCacheExports(w ShellWriter, variables map[string]string) string {
	return w.DotEnvVariables(gitlabCacheEnvFileName, variables)
}
```

**File:** shells/abstract.go (L1554-1572)
```go
	metadata := map[string]string{
		"cachekey": cacheConfig.HumanKey,
	}

	if info.Build.Runner.Cache != nil && info.Build.Runner.Cache.MaxUploadedArchiveSize > 0 {
		args = append(
			args,
			"--max-uploaded-archive-size",
			strconv.FormatInt(info.Build.Runner.Cache.MaxUploadedArchiveSize, 10),
		)
	}

	env := map[string]string{}

	// We pass the metadata via environment rather than via CLI flags, so that we are backwards compatible, e.g. for
	// user who have pinned the helper image / helper binary to an older version.
	// Note: Marshaling map[string]string wont error ever, thus we ignore the error here.
	metaJsonBlob, _ := json.Marshal(metadata)
	env["CACHE_METADATA"] = string(metaJsonBlob)
```

**File:** shells/bash.go (L247-258)
```go
func (b *BashWriter) DotEnvVariables(baseFilename string, variables map[string]string) string {
	dotEnvFile := b.TmpFile(baseFilename)

	var sb strings.Builder
	fmt.Fprintf(&sb, "cat << EOF > %s\n", dotEnvFile)
	sb.WriteString(helpers.DotEnvEscape(variables))
	sb.WriteString("EOF\n")

	b.Line(sb.String())

	return dotEnvFile
}
```

**File:** shells/bash.go (L447-453)
```go
func (b *BashWriter) escape(input string) string {
	if b.usePosixEscape {
		return helpers.PosixShellEscape(input)
	}

	return helpers.ShellEscape(input)
}
```

**File:** helpers/shell_escape.go (L139-144)
```go
var escapeDotEnvValue = strings.NewReplacer(
	"\\", "\\\\", // Escape backslashes
	"\"", "\\\"", // Escape double quotes
	"\n", "\\n", // Escape newlines
	"\r", "\\r", // Escape carriage returns
).Replace
```

**File:** shells/powershell.go (L465-472)
```go
func (p *PsWriter) DotEnvVariables(baseFilename string, variables map[string]string) string {
	p.MkDir(p.TemporaryPath)
	dotEnvFile := p.TmpFile(baseFilename)

	p.Linef("[System.IO.File]::WriteAllText(%s, @\"\n%s\n\"@)", p.resolvePath(dotEnvFile), helpers.DotEnvEscape(variables))

	return dotEnvFile
}
```
