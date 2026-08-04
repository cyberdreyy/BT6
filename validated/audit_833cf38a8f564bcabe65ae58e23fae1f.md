Confirmed: `cachekey.Sanitize` only normalizes path separators/traversal and trims trailing whitespace — it does not strip `$`, `(`, `)`, or backticks. So a cache key like `foo$(id)/bar` survives sanitization unchanged (as long as it's non-empty after cleaning).

### Title
Cache metadata/cache-key values reach an unquoted `<<EOF` heredoc in `BashWriter.DotEnvVariables`, enabling command substitution execution - ([File: shells/bash.go])

### Summary
`BashWriter.DotEnvVariables` (`shells/bash.go:247-258`) emits `cat << EOF > <file>` with an **unquoted** heredoc delimiter, then writes escaped values inside it. `DotEnvEscape` (`helpers/shell_escape.go:139-167`) only escapes backslash, double-quote, `\n`, and `\r` — it does not escape `$` or backticks. Because the heredoc delimiter is unquoted, bash performs command substitution (`$(...)`/backticks) and parameter expansion on the heredoc body before it is ever written to the file or consumed by `--env-file`, so attacker-controlled cache-key text (which flows into `CACHE_METADATA`'s `cachekey` value via `cacheConfig.HumanKey`) can execute arbitrary shell commands directly in the runner-generated build script.

### Finding Description
`AbstractShell.addCacheUploadCommand` (`shells/abstract.go:1539-1601`) builds `metadata := map[string]string{"cachekey": cacheConfig.HumanKey}`, JSON-marshals it into `env["CACHE_METADATA"]`, and calls `b.writeCacheExports(w, env)` → `w.DotEnvVariables(gitlabCacheEnvFileName, variables)` (`shells/abstract.go:616-618`). The same path exists for cache extraction in `addExtractCacheCommand` (`shells/abstract.go:337-363`).

`cacheConfig.HumanKey` is derived from the job's `cache: key:` field (or job name/git ref default), expanded via `build.GetAllVariables().ExpandValue(userKey)` and passed through `cachekey.Sanitize` (`shells/abstract.go:128-171`, `cache/cachekey/cachekey.go:20-57`). `Sanitize` only rewrites `%2f`/`%2e`/backslashes and trims path traversal/whitespace — it performs **no shell-metacharacter filtering**. A pipeline author fully controls `cache: key:` in `.gitlab-ci.yml`, so `HumanKey` (and thus the `cachekey` field inside the `CACHE_METADATA` JSON value) can contain `$(...)` or backticks unmodified.

`BashWriter.DotEnvVariables`:
```go
fmt.Fprintf(&sb, "cat << EOF > %s\n", dotEnvFile)
sb.WriteString(helpers.DotEnvEscape(variables))
sb.WriteString("EOF\n")
```
uses an unquoted `EOF` terminator. In POSIX shells, an unquoted heredoc delimiter means the body undergoes command substitution, variable expansion, and backslash processing by the shell *before* `cat` even sees it — this is standard bash heredoc semantics, distinct from `<<'EOF'` which suppresses expansion. `DotEnvEscape` escapes only `\`, `"`, `\n`, `\r` (`helpers/shell_escape.go:139-144`), leaving `$` and backtick untouched. Consequently a value such as `KEY="$(whoami > /tmp/pwned)"` inside the heredoc causes bash to execute `whoami > /tmp/pwned` at script-generation/consumption time, writing its output into the very file that becomes `CACHE_METADATA`'s value — and more importantly, arbitrary commands with side effects execute in the runner/build shell context, not just inert data substitution.

This defeats the intended invariant: cache metadata is supposed to be inert JSON data consumed by `--env-file`/`godotenv.Read` (`commands/helpers/cache_env.go:10-27`), but the vulnerability triggers *before* that consumption, at the point the env file itself is generated inside the job's bash script.

Existing protections reviewed and found insufficient:
- `cachekey.Sanitize` — normalizes path separators only, no shell-metacharacter escaping.
- `DotEnvEscape` — escapes quotes/backslash/newlines but not `$`/backtick, and relies on the (false) assumption that the surrounding heredoc is inert.
- `ShellEscape`/`PosixShellEscape` in the same file properly escape `$` for other contexts (see `posixModeTable` mapping `` ` `` and `$`), but this function is not used for `DotEnvVariables`, and even if it were, wrapping a value in `PosixShellEscape`-style double quotes still would not stop expansion inside a heredoc — only a quoted heredoc delimiter (`<<'EOF'`) prevents expansion.

Note: PowerShell's `PsWriter.DotEnvVariables` (`shells/powershell.go:465-472`) uses a `[System.IO.File]::WriteAllText` .NET call with a PowerShell here-string (`@"..."@`), which *does* perform PowerShell variable/subexpression expansion (`$(...)`) unless escaped — `DotEnvEscape` does not escape `$` there either, so the PowerShell path is potentially exposed to the analogous issue, though confirming exploitability there needs further verification of PowerShell here-string expansion rules for `$()`.

### Impact Explanation
An unprivileged pipeline author who controls `cache: key:` (or, with `HashCacheKeys` FF disabled and a key that still passes `Sanitize`) can achieve command execution inside the runner-generated bash script that writes the cache env/metadata file — i.e., runner-side command execution in the build job's shell context, using only a `.gitlab-ci.yml` cache-key value. This matches the scoped impact: "runner-side command execution via poisoned cache metadata/env file."

### Likelihood Explanation
Feasible with normal pipeline authoring privileges: no admin access, no leaked keys, no cluster compromise. Preconditions are just: (1) attacker can set `cache: key:` in a `.gitlab-ci.yml` (standard capability of any project member permitted to run pipelines), and (2) the crafted key passes `cachekey.Sanitize` (i.e., is non-empty after trimming/traversal resolution) — e.g. `foo$(touch pwned)bar` satisfies this trivially. Repeatable on every job that has a cache block configured with such a key, on any shell executor using the Bash writer (Linux shell/docker/docker-machine/etc. that route through `AbstractShell`).

### Recommendation
1. In `shells/bash.go`'s `DotEnvVariables`, use a quoted heredoc delimiter (`cat << 'EOF' > %s`) so the shell performs no expansion on the body at all — this is the standard fix for embedding untrusted data in a heredoc.
2. Defense in depth: extend `DotEnvEscape` (or add a bash-specific escaper) to also neutralize `$` and backticks when the target consumer is a shell heredoc, or route the value through `ShellEscape`/`PosixShellEscape` before embedding.
3. Add a regression test asserting that `DotEnvVariables` output, when actually executed by `bash`, never executes injected `$(...)`/backtick payloads (write generated script to a temp file, run it with a sentinel side-effect command, assert the sentinel file is never created).

### Proof of Concept
```go
// shells/bash_test.go
func Test_BashWriter_DotEnvVariables_NoCommandInjection(t *testing.T) {
    w := BashWriter{TemporaryPath: "foo/bar"}
    payload := "$(touch /tmp/pwned_by_cache_key)"
    w.DotEnvVariables("test", map[string]string{"CACHE_METADATA": `{"cachekey":"` + payload + `"}`})
    script := w.Finish(false) // or w.String(), depending on API

    // Write the generated script and actually execute it with bash to prove/disprove exploitation.
    tmp, _ := os.MkdirTemp("", "poc")
    defer os.RemoveAll(tmp)
    scriptPath := filepath.Join(tmp, "job.sh")
    _ = os.WriteFile(scriptPath, []byte("#!/bin/bash\ncd "+tmp+"\n"+script), 0755)

    cmd := exec.Command("bash", scriptPath)
    _ = cmd.Run()

    _, err := os.Stat("/tmp/pwned_by_cache_key")
    assert.Error(t, err, "command substitution in cache key must NOT execute when writing the env file")
    _ = os.Remove("/tmp/pwned_by_cache_key")
}
```
Expected (pre-fix): the file `/tmp/pwned_by_cache_key` is created — proving the vulnerability. After applying the quoted-heredoc fix, the assertion passes (file is never created). [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

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

**File:** helpers/shell_escape.go (L136-167)
```go
// The gotdotenv parser unescapes newlines and other characters:
// https://github.com/joho/godotenv/blob/3a7a19020151b45a29896c9142723efe5b11a061/parser.go#L193-L206
// Note that \t is not on the list.
var escapeDotEnvValue = strings.NewReplacer(
	"\\", "\\\\", // Escape backslashes
	"\"", "\\\"", // Escape double quotes
	"\n", "\\n", // Escape newlines
	"\r", "\\r", // Escape carriage returns
).Replace

func DotEnvEscape(variables map[string]string) string {
	var sb strings.Builder

	// Sort variables to get deterministic output
	keys := make([]string, 0, len(variables))
	for key := range variables {
		keys = append(keys, key)
	}
	sort.Strings(keys)

	for _, key := range keys {
		if !isValidDotEnvKey(key) {
			// Skip invalid keys
			continue
		}

		value := variables[key]
		fmt.Fprintf(&sb, "%s=\"%s\"\n", key, escapeDotEnvValue(value))
	}

	return sb.String()
}
```

**File:** shells/abstract.go (L128-171)
```go
// newCacheConfig creates a cacheConfig for a provided build and userKey.
// If the userKey is empty, it is defaulted to `${jobName}/${gitRef}`.
// Based on the build configuration (ie. FFs), the cacheConfig provides either a sanitized/human-readable cache
// key, or raw/hashed cache key.
// Additionally, keyChecks can be provided, which validate cache keys just after sanitation.
func newCacheConfig(build *common.Build, userKey string, keyChecks ...func(string) bool) (*cacheConfig, string, error) {
	if build.CacheDir == "" {
		return nil, "", fmt.Errorf("unset cache directory")
	}

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

	var warning string
	humanKey, err := sanitizer(rawKey)
	switch {
	case err != nil:
		warning = err.Error()
	case humanKey != rawKey:
		warning = fmt.Sprintf("cache key %q sanitized to %q", rawKey, humanKey)
	}

	for _, check := range keyChecks {
		if !check(humanKey) {
			// if a key check does not succeed, we drop out immediately
			return nil, warning, nil
		}
	}

	if humanKey == "" {
		return nil, warning, fmt.Errorf("empty cache key")
	}

	hashedKey := hasher(humanKey)
```

**File:** shells/abstract.go (L616-618)
```go
func (b *AbstractShell) writeCacheExports(w ShellWriter, variables map[string]string) string {
	return w.DotEnvVariables(gitlabCacheEnvFileName, variables)
}
```

**File:** shells/abstract.go (L1539-1601)
```go
func (b *AbstractShell) addCacheUploadCommand(
	ctx context.Context,
	w ShellWriter,
	info common.ShellScriptInfo,
	cacheConfig cacheConfig,
	archiverArgs []string,
) {
	// add metadata to the local metadata file and for GoCloud uploads
	args := []string{
		"cache-archiver",
		"--file", cacheConfig.ArchiveFile,
		"--alternate-file", cacheConfig.AlternateArchiveFile,
		"--timeout", strconv.Itoa(info.Build.GetCacheRequestTimeout()),
	}

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

	args = append(args, archiverArgs...)

	// Generate cache upload address
	extraArgs, extraEnv, err := getCacheUploadURLAndEnv(ctx, info.Build, cacheConfig.HashedKey, metadata)
	args = append(args, extraArgs...)
	maps.Copy(env, extraEnv)

	if err != nil {
		w.Warningf("Unable to generate cache upload environment: %v", err)
	}

	// Execute cache-archiver command. Failure is not fatal.
	b.guardRunnerCommand(w, info.RunnerCommand, "Creating cache", func() {
		w.Noticef("Creating cache %s...", cacheConfig.HumanKey)

		if env != nil {
			cacheEnvFilename := b.writeCacheExports(w, env)
			args = append(args, "--env-file", cacheEnvFilename)
			defer w.RmFile(cacheEnvFilename)
		}

		w.IfCmdWithOutput(info.RunnerCommand, args...)
		w.Noticef("Created cache")
		w.Else()
		w.Warningf("Failed to create cache")
		w.EndIf()
	})
}
```

**File:** cache/cachekey/cachekey.go (L20-57)
```go
// Sanitize validates and normalises a cache key.
// Cache keys may contain path separators. The function:
//   - decodes URL-encoded '/' (%2f) and '.' (%2e) characters
//   - replaces all '\' with '/'
//   - resolves path traversals (., ..) within a virtual root
//   - strips trailing whitespace from the rightmost path segments,
//     removing any that become empty after trimming
func Sanitize(cacheKey string) (string, error) {
	if cacheKey == "" {
		return "", nil
	}

	// Decode percent-encoded chars and normalise separators, then
	// resolve traversals against a virtual root so ".." can never
	// escape beyond the root.
	cleaned := path.Clean("/" + normaliser.Replace(cacheKey))

	// Strip the leading "/" we added, split into segments, then walk
	// backwards trimming trailing whitespace from the rightmost
	// segments—dropping any that become empty.
	parts := strings.Split(cleaned[1:], "/")
	n := len(parts)
	for n > 0 {
		parts[n-1] = strings.TrimRightFunc(parts[n-1], unicode.IsSpace)
		if parts[n-1] != "" {
			break
		}
		n--
	}

	key := strings.Join(parts[:n], "/")

	if key == "" {
		return "", fmt.Errorf("cache key %q could not be sanitized", cacheKey)
	}

	return key, nil
}
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
