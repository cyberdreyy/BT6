### Title
Path traversal in `PsWriter.Variable`/`TmpFile` for File-type secrets allows arbitrary file write outside `TemporaryPath` on Windows shells - ([File: shells/powershell.go])

### Summary
`PsWriter.Variable` builds the temp-file path for File-type secrets/variables by joining `p.TemporaryPath` with the raw, attacker-controlled `variable.Key` via `TmpFile`/`Join`/`cleanPath`, which rely on `filepath.Join`/`path.Join` and `[System.IO.File]::WriteAllText`. Neither of these functions rejects `..` traversal segments or absolute/UNC-style paths, and no post-hoc containment check (`isTmpFile`) is applied to the File-branch's generated path. If the secret/variable key contains traversal sequences, the resulting `WriteAllText` target can land outside the intended `TemporaryPath`.

### Finding Description
`defaultSecretsResolver.handleSecret` (`common/secrets.go:116-146`) builds a `spec.Variable{Key: variableKey, ..., File: secret.IsFile()}` where `variableKey` is taken verbatim from the job's `secrets:` map key [1](#0-0) . This value is not sanitized anywhere in the Runner code for filesystem-unsafe characters (no `..`, `/`, `\`, drive-letter, or UNC checks).

That `Variable` is later passed to `PsWriter.Variable` on the PowerShell/pwsh shell path [2](#0-1) . For `variable.File == true`, the code computes:
```
variableFile := p.TmpFile(variable.Key)
...
[System.IO.File]::WriteAllText(p.resolvePath(variableFile), ...)
```
`TmpFile` calls `p.Join(p.TemporaryPath, name)` and, when `resolvePaths` is false, additionally `p.cleanPath(...)` which calls `p.Absolute(...)` [3](#0-2) . `Join` uses `path.Join` (resolvePaths on) or `filepath.Join` (resolvePaths off) [4](#0-3) . Both of these Go stdlib functions lexically `Clean` the result and will happily collapse leading `..` segments to walk above the base directory — they provide no containment guarantee, e.g. `filepath.Join("C:\\base\\tmp", "..\\..\\Windows\\System32\\evil.ps1")` cleans to `C:\Windows\System32\evil.ps1`. When `resolvePaths` is true, `PsWriter.resolvePath` instead emits `$ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath(...)`, which resolves relative to the current PowerShell working directory and likewise does not clamp `..` segments to a root, so an attacker-supplied `..\..\` prefix (or absolute path / UNC path) is passed straight through to `WriteAllText` in both configurations.

The only containment helper in the file, `isTmpFile` (`strings.HasPrefix(path, p.TemporaryPath)`) [5](#0-4) , is applied only in the `else` (non-File) branch of `Variable` to an already-resolved variable *value*, not to the File-branch's constructed `variableFile` path. So there is no check anywhere in this call chain that verifies the final `WriteAllText` target actually stays under `p.TemporaryPath`.

The remaining open question — and the reason this is not a slam-dunk without further verification — is whether GitLab Rails/CI schema validation of `secrets:` map keys (which become the variable name) restricts the key to a safe identifier pattern (e.g., `^[A-Za-z_][A-Za-z0-9_]*$`) before the job payload ever reaches the Runner. That validation, if present, would happen entirely outside this repository and I could not confirm or rule it out from the Runner codebase alone. What is confirmed from the Runner code is that the Runner itself performs no defense-in-depth sanitization of `variable.Key` before using it as a filesystem path component, so if any code path (custom executor, current or future GitLab API versions, or a bug in upstream validation) allows a non-conforming key through, this Windows-shell arbitrary-file-write is directly reachable.

### Impact Explanation
If reachable, a File-type secret/variable whose key contains `..` traversal segments (or an absolute/UNC path) causes the generated PowerShell/pwsh script to call `[System.IO.File]::WriteAllText` against a path outside the job's `TemporaryPath`, i.e. outside the intended build workspace on the Windows shell/docker-windows executor host or container filesystem — a concrete violation of the "file operations must stay within intended build/cache/artifact roots" invariant.

### Likelihood Explanation
Preconditions: (1) a File-type secret or variable whose `Key` is attacker-influenced with traversal characters, (2) PowerShell or pwsh shell selected, (3) `resolvePaths` on or off (both paths lack containment). Feasibility hinges entirely on whether upstream (GitLab Rails schema/API, or the runner's own job-payload deserialization) constrains variable/secret keys to identifier-safe characters before they reach `handleSecret`/`PsWriter.Variable`. I was not able to find such validation inside the Runner codebase itself, meaning the Runner does not independently enforce this invariant — it is exposed if any upstream boundary is bypassed or if there's another producer of `spec.Variable`/`spec.Secret` (e.g., custom executor jobs) that doesn't apply the same restriction.

### Recommendation
Add an explicit sanitization/validation step for `variable.Key` before using it as a filesystem path component in `PsWriter.TmpFile` (and the equivalent Bash-shell `tmpFile`), e.g., reject or strip path separators, `..` segments, and absolute-path/UNC prefixes; alternatively, base the temp filename on a hash/UUID rather than the raw key, and independently verify with `isTmpFile`/`filepath.Rel` that the final resolved path is contained within `p.TemporaryPath` before emitting the `WriteAllText` command, failing the job if it is not.

### Proof of Concept
Go unit test in `shells/powershell_test.go`:
```go
func TestPsWriterVariable_FileKeyTraversal(t *testing.T) {
    w := &PsWriter{TemporaryPath: `C:\build\tmp`, EOL: "\r\n"}
    w.Variable(spec.Variable{
        Key:   `..\..\Windows\System32\evil.ps1`,
        Value: "malicious content",
        File:  true,
    })
    script := w.String()
    // Assert the WriteAllText target is confined to TemporaryPath
    assert.NotContains(t, script, `Windows\System32`)
    assert.True(t, strings.Contains(script, `C:\build\tmp`))
}
```
Run this with `resolvePaths` both `true` and `false`; expect the assertion to fail against current code (the generated `WriteAllText` call targets a path resolving outside `C:\build\tmp`), confirming the traversal reaches the emitted script.

### Citations

**File:** common/secrets.go (L100-145)
```go
	for variableKey, secret := range secrets {
		r.logger.Println(fmt.Sprintf("Resolving secret %q...", variableKey))

		v, err := r.handleSecret(variableKey, secret)
		if err != nil {
			return nil, err
		}

		if v != nil {
			variables = append(variables, *v)
		}
	}

	return variables, nil
}

func (r *defaultSecretsResolver) handleSecret(variableKey string, secret spec.Secret) (*spec.Variable, error) {
	sr, err := r.secretResolverRegistry.GetFor(secret)
	if err != nil {
		r.logger.Warningln(fmt.Sprintf("Not resolved: %v", err))
		return nil, nil
	}

	r.logger.Println(fmt.Sprintf("Using %q secret resolver...", sr.Name()))

	value, err := sr.Resolve()
	if errors.Is(err, ErrSecretNotFound) {
		if !r.featureFlagOn(featureflags.EnableSecretResolvingFailsIfMissing) {
			err = nil
		} else {
			err = fmt.Errorf("%w: %v", err, variableKey)
		}
	}
	if err != nil {
		return nil, err
	}

	variable := &spec.Variable{
		Key:    variableKey,
		Value:  value,
		File:   secret.IsFile(),
		Masked: true,
		Raw:    true,
	}

	return variable, nil
```

**File:** shells/powershell.go (L406-420)
```go
func (p *PsWriter) TmpFile(name string) string {
	if p.resolvePaths {
		return p.Join(p.TemporaryPath, name)
	}

	return p.cleanPath(p.Join(p.TemporaryPath, name))
}

func (p *PsWriter) cleanPath(name string) string {
	if p.resolvePaths {
		return name
	}

	return p.fromSlash(p.Absolute(name))
}
```

**File:** shells/powershell.go (L434-436)
```go
func (p *PsWriter) isTmpFile(path string) bool {
	return strings.HasPrefix(path, p.TemporaryPath)
}
```

**File:** shells/powershell.go (L438-447)
```go
func (p *PsWriter) Variable(variable spec.Variable) {
	if variable.File {
		variableFile := p.TmpFile(variable.Key)
		p.MkDir(p.TemporaryPath)
		p.Linef(
			"[System.IO.File]::WriteAllText(%s, %s)",
			p.resolvePath(variableFile),
			psQuoteVariable(variable.Value),
		)
		p.Linef("${%s}=%s", variable.Key, p.resolvePath(variableFile))
```

**File:** shells/powershell.go (L651-659)
```go
func (p *PsWriter) Join(elem ...string) string {
	if p.resolvePaths {
		// We rely on the resolve function and always use forward slashes
		// when joining paths.
		return path.Join(elem...)
	}

	return filepath.Join(elem...)
}
```
