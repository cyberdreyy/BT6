### Title
Path traversal / shell metacharacter injection via `variable.Key` in file-type CI/CD variable writes - (File: `shells/bash.go`)

### Summary
`BashWriter.Variable` builds the destination path for a `file`-type variable via `b.TmpFile(variable.Key)`, which is `path.Clean(path.Join(TemporaryPath, Key))`, and then embeds that path in a `%q`-quoted (Go-style) bash string. `path.Join`/`path.Clean` collapse `..` segments lexically, so a `Key` containing `../` sequences resolves to a path outside `TemporaryPath`, and characters such as `` ` ``/`$( )` that are meaningful inside bash double quotes are not escaped by Go's `%q` (which only guards Go-string syntax, not shell syntax).

### Finding Description
`BashWriter.Variable` (`shells/bash.go:229-241`) handles `variable.File == true` like this: [1](#0-0) 
- `variableFile := b.TmpFile(variable.Key)` calls `TmpFile`/`cleanPath`/`Absolute` (`shells/bash.go:211-217`, `361-366`), which is just `path.Clean(path.Join(TemporaryPath, Key))` made absolute — no confinement check exists that rejects `..` components or verifies the resulting path stays under `TemporaryPath`.
- The three emitted lines (`mkdir -p %q`, `printf ... > %q`, `export ...=%q`) use Go's `%q` verb. `%q` escapes characters that are special to *Go string literal syntax* (`"`, `\`, control chars) — it does **not** escape shell metacharacters such as backtick, `$`, or `(`/`)`. Inside a bash double-quoted string, `` ` `` and `$(...)` still trigger command substitution, and `..` path segments are not neutralized at all since `%q` operates on the already-joined path string, not on raw `Key` characters requiring shell-quoting logic.
- `b.isTmpFile` (`shells/bash.go:225-227`) is explicitly documented as "Intended to be used on unmodified paths only" and is not invoked in the `variable.File` branch to validate that `variableFile` still lives under `TemporaryPath` before it's used.

Attacker input: `variable.Key` originates from job/pipeline-controlled CI/CD variable definitions marked as file-type. If such a Key contains `../../../../tmp/evil` (or another writable absolute-resolving relative path), `path.Join` mathematically resolves it outside the job's `TemporaryPath`, and the generated script writes attacker-controlled `variable.Value` content to that external location. If Key instead contains backticks or `$( )`, the generated double-quoted bash fragment executes arbitrary shell commands at script-generation/interpretation time, since those characters pass through `%q` unescaped.

No other code path in the reviewed flow (`common/spec/variables.go` `tmpFile`, `Variables.value`) performs Key sanitization either — it performs the identical unguarded `path.Join(TempProjectDirVariableKey_value, Key)`.

### Impact Explanation
Concrete scoped impact is arbitrary file write outside the job's `TemporaryPath`/workspace root on the runner/helper filesystem (or, with `` ` ``/`$()` Key values, command injection during script execution) — both directly violate the stated invariant "File operations must stay within intended build/cache/artifact roots." The severity depends on executor/filesystem layout (e.g., writing into `/tmp` inside a container, or into a shared path in shell-executor setups), but the confinement check itself is missing regardless of executor.

### Likelihood Explanation
Whether this is practically exploitable hinges on whether GitLab (the coordinating server) validates variable Key format (typically enforced server-side as `\A[a-zA-Z_][a-zA-Z0-9_]*\z`) before ever sending job payloads to the Runner. I was not able to locate any Key-format validation inside this Runner repository itself — `common/spec/variables.go` and `shells/bash.go` both trust `Key` verbatim, and only `helpers/shell_escape.go`'s `isValidDotEnvKey` (used solely for dotenv-artifact-sourced variables, not for the general `Variable.File` path) enforces an identifier pattern. Since the Runner codebase has no independent defense-in-depth check here, if any accepted variable source (job payload field, dotenv-derived variable not going through `isValidDotEnvKey`, or a future/alternate GitLab variable API) permits non-identifier Keys, this becomes directly exploitable. This is a genuine missing-check bug in the Runner regardless of the upstream GitLab validation, because Runner should not rely solely on server-side trust for a security boundary that is documented as an invariant ("File operations must stay within intended build/cache/artifact roots").

### Recommendation
In `BashWriter.Variable`'s file branch, validate `variable.Key` against a strict allow-list pattern (e.g., `^[A-Za-z_][A-Za-z0-9_]*$`, consistent with `isValidDotEnvKey` in `helpers/shell_escape.go`) before calling `b.TmpFile`, and/or verify after `TmpFile` that the resulting `variableFile` still has `TemporaryPath` as a strict prefix (analogous to `isTmpFile`, but applied defensively to the cleaned path) — rejecting/erroring the job instead of continuing when the check fails. Additionally, since `%q` is not a shell-safe quoting mechanism, use `b.escape` (the shell-escaping function) for the path argument, not raw `%q`, in the `printf`/`export` lines.

### Proof of Concept
```go
// shells/bash_test.go
func TestBash_VariableFile_PathTraversal(t *testing.T) {
    writer := &BashWriter{TemporaryPath: "/builds/project-1/tmp"}
    writer.Variable(spec.Variable{
        Key:   "../../../../tmp/evil",
        Value: "attacker-controlled-content",
        File:  true,
    })
    out := writer.String()
    // Assert the emitted script never references a path outside TemporaryPath
    assert.NotContains(t, out, "/tmp/evil")
    assert.Contains(t, out, writer.TemporaryPath)
}

func TestBash_VariableFile_BacktickInjection(t *testing.T) {
    writer := &BashWriter{TemporaryPath: "/builds/project-1/tmp"}
    writer.Variable(spec.Variable{
        Key:   "`touch /tmp/pwned`",
        Value: "x",
        File:  true,
    })
    out := writer.String()
    // Assert no unescaped backtick/$( ) survives into the generated script
    assert.NotContains(t, out, "`touch")
}
```
Both assertions currently fail against the code at `shells/bash.go:229-241`, confirming the missing confinement/escaping.

### Citations

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
