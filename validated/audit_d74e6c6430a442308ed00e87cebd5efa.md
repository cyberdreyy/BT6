### Title
Unsanitized `variable.Key` used as file-variable filename allows path traversal outside the build's temporary/variables directory - ([File: shells/bash.go], [File: shells/powershell.go], [File: common/secrets.go])

### Summary
`defaultSecretsResolver.handleSecret` copies the `secrets:` map key (`variableKey`) verbatim into `spec.Variable.Key` with no character/format validation. When the secret is file-backed (`File: true`), the shell writers (`BashWriter.Variable`, `PsWriter.Variable`) build the on-disk path with `TmpFile(variable.Key)`, which does a plain `path.Join(TemporaryPath, name)` (bash) / equivalent join (PowerShell) followed by path cleaning — but the cleaning collapses `..` segments rather than rejecting/containing them, so a crafted key can make the resulting path escape `TemporaryPath`.

### Finding Description
`defaultSecretsResolver.handleSecret` builds the variable straight from the map key with no validation: [1](#0-0) 

That `Variable` (with `File: secret.IsFile()`) is later emitted by the shell writer. In Bash, `TmpFile` computes the file path as: [2](#0-1) 

`path.Join` calls `path.Clean` internally, which resolves `..` components against the preceding path segments instead of rejecting them, so `TmpFile("../../etc/foo")` (or a name with embedded path separators) can navigate outside `TemporaryPath` if `cleanPath`/`Absolute` doesn't re-confine the result to the temp directory (no such confinement check was found). The same key is also used directly for the exported shell variable name and, in cleanup, to build the removal path: [3](#0-2) 

PowerShell mirrors this pattern, similarly trusting `variable.Key`: [4](#0-3) 

No code path in `common/secrets.go`, `common/spec` (Variable/Variables), or the shell writers validates that `Key` is a safe filename/identifier (e.g., restricted to `[A-Za-z_][A-Za-z0-9_]*`) before it is used to build a filesystem path.

The critical caveat: in real deployments, the `secrets:` key in `.gitlab-ci.yml` is also used as the CI/CD environment variable name, and GitLab Rails' CI/CD configuration linter enforces variable-name syntax (letters/digits/underscore) before the job is ever dispatched to a Runner. That upstream validation is what normally prevents a `variableKey` containing `/` or `..` from ever reaching the Runner. This repository, however, contains no independent defense at the Runner boundary — if a payload with such a key ever reaches `handleSecret` (e.g., through a non-standard job source, a bug/regression in the upstream validator, or a custom orchestrator sending crafted job payloads), Runner will not stop it.

### Impact Explanation
If reached, a crafted `variableKey`/secret name causes the file-variable writer to place the resolved secret value at an attacker-chosen path outside `TemporaryPath` (the build's variables/tmp directory), which is a real file-write-path-escape primitive with attacker-influenced content (the secret value) — matching the scoped impact of "unauthorized file write outside build root."

### Likelihood Explanation
Exploitability from a "pure GitLab pipeline author" perspective is low-to-none in the standard flow because GitLab's server-side CI/CD configuration validation restricts variable/secret names to identifier-safe characters before the job reaches the Runner, so the traversal payload described in the preconditions (`../../etc/foo`) would normally never reach `handleSecret`. The underlying Runner-side code, however, performs no independent validation of `variable.Key`, so the protection is entirely external to this repository and not defense-in-depth. This makes the finding valid as a missing-check/defense-in-depth issue in the Runner code, but its likelihood of being reachable by an ordinary pipeline author through the documented `.gitlab-ci.yml` `secrets:` syntax is low, since it depends on bypassing the upstream name-format validation.

### Recommendation
Add explicit validation of `spec.Variable.Key` (and the `secrets:` map key specifically) at the point it is set in `defaultSecretsResolver.handleSecret`, restricting it to a safe identifier pattern (e.g. `^[A-Za-z_][A-Za-z0-9_]*$`), and/or harden `TmpFile`/file-variable writers in `shells/bash.go` and `shells/powershell.go` to reject or sanitize any resolved path that, after cleaning, is not contained within `TemporaryPath` (e.g., verify with `filepath.Rel` that no `..` prefix remains, or use `filepath.Base(name)` to strip directory components entirely before joining).

### Proof of Concept
```go
// shells/bash_test.go (illustrative)
func TestBashWriter_Variable_FileKeyTraversal(t *testing.T) {
    w := &BashWriter{TemporaryPath: "/builds/project/tmp"}
    w.Variable(spec.Variable{
        Key:   "../../../../etc/cron.d/evil",
        Value: "* * * * * root touch /tmp/pwned",
        File:  true,
    })
    out := w.String() // or however lines are collected
    // Assert the produced script does not write outside TemporaryPath
    assert.NotContains(t, out, "/etc/cron.d/evil")
}
```
Expected (current) result: the test fails because `TmpFile` produces a path outside `/builds/project/tmp`, confirming the missing containment check. A corresponding fix should make this test pass by clamping/rejecting the traversal.

### Citations

**File:** common/secrets.go (L137-143)
```go
	variable := &spec.Variable{
		Key:    variableKey,
		Value:  value,
		File:   secret.IsFile(),
		Masked: true,
		Raw:    true,
	}
```

**File:** shells/bash.go (L211-217)
```go
func (b *BashWriter) TmpFile(name string) string {
	return b.cleanPath(path.Join(b.TemporaryPath, name))
}

func (b *BashWriter) cleanPath(name string) string {
	return b.Absolute(name)
}
```

**File:** shells/abstract.go (L1837-1842)
```go
	for _, variable := range info.Build.GetAllVariables() {
		if !variable.File {
			continue
		}
		w.RmFile(w.TmpFile(variable.Key))
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
