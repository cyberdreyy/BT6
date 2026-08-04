### Title
Attacker-controlled `File`-variable keys let `GenerateScript`-emitted cleanup escape the job's temporary-file root - (File: shells/powershell.go)

### Summary
`PsWriter.TmpFile` (and its Bash equivalent) build a cleanup path by naively joining `TemporaryPath` with a caller-supplied name via `filepath.Join`/`path.Join`, which silently collapses `..` segments instead of rejecting them. `AbstractShell.writeCleanupScript` feeds every `File`-type job variable's `Key` directly into `w.RmFile(w.TmpFile(variable.Key))`, so a variable key containing traversal segments causes the generated PowerShell/Bash cleanup script to delete a path outside the job's `.tmp` root.

### Finding Description
`PsWriter.TmpFile` computes the delete target as: [1](#0-0) 
`Join` is a thin wrapper over `filepath.Join`/`path.Join`: [2](#0-1) 
Both underlying `Join` implementations call `Clean()`, which *resolves* `..` components rather than rejecting them — so `filepath.Join(".tmp", "../../../../etc/passwd")` produces a path that walks out of `.tmp`. `Absolute()` then anchors any resulting relative path to `$CurrentDirectory` in the emitted PowerShell, so the traversal survives into the final script as `Join`/`Resolve-Path`-friendly `..` segments: [3](#0-2) 

This unsanitized `TmpFile` helper is fed attacker-influenced data in two places in `AbstractShell.writeCleanupScript`, which is called from `GenerateScript`'s script-writing pipeline for the cleanup stage: [4](#0-3) 
For every `File`-type variable, `variable.Key` — not `variable.Value` — is passed straight into `TmpFile`/`RmFile` with no character allow-listing or path-traversal check. The same unsanitized `Key` is also used earlier when the value is first written to disk: [5](#0-4) 
So a single malicious `File`-flagged variable name gives both a write primitive (`Variable()`, during script setup) and a matching delete primitive (`writeCleanupScript`, during cleanup) that both resolve outside the intended `.tmp` build root.

Existing protections that do not stop this: `TmpFile`/`Join`/`Absolute` perform only string concatenation/cleaning and never verify the joined result still has `TemporaryPath` as a prefix; `isTmpFile` (`strings.HasPrefix(path, p.TemporaryPath)`) is only used to decide whether to re-clean a variable's *value*, not to validate the *key*-derived filename before deletion: [6](#0-5) 
No other check in `GenerateScript`/`writeCleanupScript` re-validates `variable.Key` against a safe identifier pattern before it becomes a filesystem path component.

### Impact Explanation
If a `File`-type variable with a crafted `Key` (containing `../` / `..\` sequences) reaches the runner — via any source Runner accepts as `spec.Variable` without re-validating key format (e.g., dotenv-report-produced variables, or any pipeline-variable path that isn't strictly enforced upstream) — the generated cleanup script will `Remove-Item -Force` (or `rm -f` in Bash) an attacker-chosen path outside the job's `.tmp`/build root, on the runner host or shared executor filesystem. This is cross-job/host state tampering: deletion or corruption of files unrelated to the job that created the variable, matching the scoped "cross-job state tampering" impact.

### Likelihood Explanation
Requires only that the attacker can cause a `File`-type variable with an attacker-chosen `Key` string to be delivered to Runner as part of job variables (e.g., through dotenv-report-derived variables, which are pipeline/job-author controlled and are not necessarily constrained to identifier-safe characters at the Runner boundary). Runner performs no independent validation of `Key`, so if any upstream path allows non-identifier characters in the key, exploitation is deterministic and repeatable every time that job's cleanup stage runs — no race conditions or privileged access needed.

### Recommendation
Reject or sanitize `variable.Key` before using it as a filesystem-path component in `TmpFile`/`Variable`/`writeCleanupScript`: enforce a strict identifier allow-list (e.g. `^[A-Za-z_][A-Za-z0-9_]*$`) at the point variables are ingested by Runner, and additionally harden `TmpFile`/`Join` to verify (e.g. via `filepath.Rel` and rejecting results starting with `..`) that the resolved path remains within `TemporaryPath` before it is ever handed to `RmFile`/`RmDir`/`WriteAllText`.

### Proof of Concept
Go unit test targeting `PowerShell.generateScript` (or `AbstractShell.writeCleanupScript` directly, mirroring the existing `TestAbstractShell_writeCleanupBuildDirectoryScript` pattern):
1. Build a `common.ShellScriptInfo` whose `Build.Variables` contains one variable: `Key: "../../../../tmp/escape-me", File: true, Value: "x"`.
2. Call `GenerateScript(ctx, common.BuildStageCleanup, info)` for both `pwsh` and `bash` shells.
3. Assert the emitted script text contains a `Remove-Item`/`rm -f` invocation whose resolved path (after simulating `filepath.Join`) lies outside `info.Build.TmpProjectDir()` — e.g. assert `!strings.HasPrefix(resolvedDeletePath, tmpProjectDir)`.
4. As an integration-level PoC, run a real shell-executor job with a `File`-type CI variable named with `../` traversal (via a fabricated dotenv artifact or direct `spec.Variable` injection in a Runner-level integration test) and confirm a file outside the job's `.tmp` directory is deleted after the cleanup stage.

### Citations

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

**File:** shells/powershell.go (L438-457)
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
	} else {
		if p.isTmpFile(variable.Value) {
			variable.Value = p.cleanPath(variable.Value)
		}

		p.Linef("${%s}=%s", variable.Key, psQuoteVariable(variable.Value))
	}

	p.Linef("${env:%s}=${%s}", variable.Key, variable.Key)
}
```

**File:** shells/powershell.go (L638-649)
```go
func (p *PsWriter) Absolute(dir string) string {
	if p.resolvePaths {
		return dir
	}

	if filepath.IsAbs(dir) {
		return dir
	}

	p.Linef("$CurrentDirectory = (Resolve-Path .%s).Path", string(os.PathSeparator))
	return p.Join("$CurrentDirectory", dir)
}
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

**File:** shells/abstract.go (L1831-1848)
```go
func (b *AbstractShell) writeCleanupScript(_ context.Context, w ShellWriter, info common.ShellScriptInfo) error {
	w.RmFile(w.TmpFile(gitlabEnvFileName))
	w.RmFile(w.TmpFile("masking.db"))
	w.RmFile(w.TmpFile(externalGitConfigFile))
	w.RmFile(w.TmpFile(globalGitConfigSeedFile))

	for _, variable := range info.Build.GetAllVariables() {
		if !variable.File {
			continue
		}
		w.RmFile(w.TmpFile(variable.Key))
	}

	if info.Build.IsFeatureFlagOn(featureflags.EnableJobCleanup) {
		if err := b.writeCleanupBuildDirectoryScript(w, info); err != nil {
			return err
		}
	}
```
