## Analysis

`PsWriter.Variable` (`shells/powershell.go:438-457`) writes each job variable as a PowerShell curly-braced variable assignment, and it interpolates `variable.Key` directly into the script with no escaping at all:

```go
p.Linef("${%s}=%s", variable.Key, psQuoteVariable(variable.Value))
...
p.Linef("${env:%s}=${%s}", variable.Key, variable.Key)
``` [1](#0-0) 

Only `variable.Value` is escaped, via `psQuoteVariable` (double-quote + backtick escaping of `$`), and only for the value, never the key. `psSingleQuote`/`psDoubleQuote`/`psQuoteVariable` are defined at `shells/powershell.go:229-250` and are applied solely to values/arguments, not to `variable.Key`.

By contrast, `BashWriter.Variable` (`shells/bash.go:229-241`) escapes both the key and value with `b.escape(...)`:
```go
b.Linef("export %s=%s", b.escape(variable.Key), b.escape(variable.Value))
``` [2](#0-1) 
This asymmetry confirms the PowerShell path is missing key-escaping that the bash path has.

The test suite documents that arbitrary characters — including spaces and dashes — are accepted in `Key` and pass through unescaped into the `${...}` syntax, e.g. `Test_PsWriter_Variable`'s `"regular var with dashes"` case producing `${Test-Variable}=...` and `"file var"` in `TestPowershellPathResolveOperations` using `Key: "a key"` producing `${a key}=...`. [3](#0-2) [4](#0-3) 

PowerShell's `${...}` curly-brace variable-name syntax terminates on the first unescaped `}` (escaping requires a backtick before `}` inside the braces). Since `variable.Key` is inserted raw, a key containing `}` breaks out of the variable-name context and injects arbitrary PowerShell statements into whatever build stage's script the variable ends up in (prepare/build/after_script/cleanup scripts all call `Variable()` while exporting job variables).

However, before concluding this is exploitable, the critical open question is **whether variable keys reaching `Variable()` are constrained upstream** (e.g. GitLab Rails validates CI/CD variable names against `^[a-zA-Z_][a-zA-Z0-9_]*$`, and dotenv key validation exists via `isValidDotEnvKey` in `helpers/shell_escape.go:130-134`, which is used to *filter* dotenv-report-derived keys before they become variables). I was not able to fully trace every code path that constructs a `spec.Variable` (e.g. `common/spec/spec.go`, `common/build.go` variable-composition logic) to determine if there's an unconditional key-format validation gate applied to every variable before it reaches the shell writer, or whether some paths (e.g. custom variable names accepted from CI config fields, `%`-prefixed variables, or step metadata that isn't run through the dotenv validator) could carry non-identifier characters like `}` straight through to `PsWriter.Variable`.

Given:
- The dotenv-derived path is explicitly guarded (`isValidDotEnvKey`), suggesting the maintainers are aware such filtering is necessary and applied it at least there.
- I could not confirm within this session whether *all* other variable-creation paths (regular job variables from `.gitlab-ci.yml`, CI/CD settings variables) enforce identical restrictions before invoking `PsWriter.Variable`, since GitLab Rails-side validation is out of scope of this repo and I could not fully inspect `common/spec/spec.go`/`common/build.go`'s full variable pipeline in this session.

I can confirm the root-cause code defect (missing key-escaping in `PsWriter.Variable`) but cannot fully confirm end-to-end reachability with an attacker-fully-controlled `}`-containing key from an unprivileged entrypoint without further tracing of `common/spec/spec.go` and `common/build.go`. This uncertainty is material to the "Valid Only If" criteria (#3 exploit path attacker action→trigger, #4 that existing checks are insufficient).

### Title
Unescaped variable key in PowerShell `Variable` write allows `${...}` syntax breakout — ([File: shells/powershell.go])

### Summary
`PsWriter.Variable` interpolates `variable.Key` unescaped into PowerShell `${KEY}=...` assignment statements, while the equivalent Bash implementation escapes both key and value. If a variable key can contain a `}` (or backtick), a build/after-script/cleanup phase would execute attacker-supplied PowerShell statements when the script runs.

### Finding Description
`PsWriter.Variable` at `shells/powershell.go:438-457` builds lines like `${%s}=%s` and `${env:%s}=${%s}` where the first `%s` is the raw `variable.Key`. Values pass through `psQuoteVariable`/`p.resolvePath` which perform proper PowerShell escaping, but no equivalent function is ever applied to the key. PowerShell's `${...}` delimited variable-name syntax treats an unescaped `}` as the terminator of the variable name, so a key such as `KEY}; Invoke-Expression $env:MALICIOUS; ${X` would close the variable reference early and splice a new statement into the script — this statement would execute in whatever phase (prepare, build, after_script, cleanup) that `Variable()` is invoked for, potentially a phase where the attacker's original job-script injection points (bash `Command`/`escape`) were properly sanitized but this generator path was not. The Bash equivalent (`shells/bash.go:229-241`) escapes the key via `b.escape(variable.Key)`, confirming the omission in the PowerShell path is inconsistent with the codebase's own security model for the analogous function.

### Impact Explanation
If reachable with an attacker-controlled key containing `}`/backtick, this allows command execution injected into a later or differently-scoped runner-generated PowerShell stage (e.g., cleanup) beyond the user's own job script, which could run with different trust assumptions (e.g., after masking/secret-handling setup, or in a stage the attacker's own script doesn't directly control).

### Likelihood Explanation
Feasibility hinges entirely on whether any attacker-reachable path can produce a `spec.Variable.Key` containing `}` before it reaches `PsWriter.Variable`. GitLab's variable name validation is normally enforced server-side (identifier-only keys), and this repo enforces an equivalent identifier check for dotenv-derived variables (`isValidDotEnvKey`), which suggests non-identifier keys are not expected to reach this function in the common case. Without confirming all variable-construction call sites in `common/spec/spec.go`/`common/build.go` reject non-identifier keys, likelihood cannot be established with confidence in this session.

### Recommendation
Escape `variable.Key` in `PsWriter.Variable` (and any other PowerShell writer method that interpolates a variable name, e.g. `EnvVariableKey`) the same way `BashWriter.Variable` escapes keys — at minimum reject/escape `}` and backtick characters, or validate that keys match a safe identifier pattern before writing them into `${...}` syntax, defense-in-depth regardless of upstream validation guarantees.

### Proof of Concept
Go unit test in `shells/powershell_test.go` extending `Test_PsWriter_Variable`:
```go
writer := PsWriter{TemporaryPath: "C:/foo/bar"}
writer.Variable(spec.Variable{Key: `X}; Write-Output INJECTED; ${Y`, Value: "VALUE"})
```
Assertion: the produced script currently contains a bare `Write-Output INJECTED` statement outside of any `${...}` variable-name context (i.e., `assert.Contains(t, writer.String(), "; Write-Output INJECTED; ")` as a standalone statement rather than as part of an escaped/quoted literal) — proving script-syntax breakout from the key field. Combine with an integration test exercising whichever concrete code path (if confirmed) allows a non-identifier key to survive into `Job.Variables` for the PowerShell/pwsh shell, then assert the generated cleanup/after_script for that build actually executes the injected command.

### Citations

**File:** shells/powershell.go (L447-457)
```go
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

**File:** shells/bash.go (L229-241)
```go
func (b *BashWriter) Variable(variable spec.Variable) {
	if variable.File {
		variableFile := b.TmpFile(variable.Key)
		b.Linef("mkdir -p %q", helpers.ToSlash(b.TemporaryPath))
		b.Linef("printf '%%s' %s > %q", b.escape(variable.Value), variableFile)
		b.Linef("export %s=%q", b.escape(variable.Key), variableFile)
	} else {
		if b.isTmpFile(variable.Value) {
			variable.Value = b.cleanPath(variable.Value)
		}
		b.Linef("export %s=%s", b.escape(variable.Key), b.escape(variable.Value))
	}
}
```

**File:** shells/powershell_test.go (L460-471)
```go
		"file variable": {
			op: func(path string, w *PsWriter) {
				w.TemporaryPath = path
				w.Variable(spec.Variable{File: true, Key: "a key", Value: "foobar"})
			},
			template: "New-Item -ItemType directory -Force -Path $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath(\"%[1]v\") | out-null\n[System.IO.File]::WriteAllText($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath(\"%[1]v/a key\"), \"foobar\")\n${a key}=$ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath(\"%[1]v/a key\")\n${env:a key}=${a key}\n",
			expected: map[string]func(string) string{
				`path/name`:    templateReplacer(`path/name`),
				`\\unc\file`:   templateReplacer(`\\unc\file`),
				`C:\path\file`: templateReplacer(`C:\path\file`),
			},
		},
```

**File:** shells/powershell_test.go (L873-878)
```go
		"regular var with dashes": {
			variable:    spec.Variable{Key: "Test-Variable", Value: "value"},
			writer:      PsWriter{TemporaryPath: "C:/foo/bar"},
			wantLinux:   "${Test-Variable}=\"value\"${env:Test-Variable}=${Test-Variable}",
			wantWindows: "${Test-Variable}=\"value\"${env:Test-Variable}=${Test-Variable}",
		},
```
