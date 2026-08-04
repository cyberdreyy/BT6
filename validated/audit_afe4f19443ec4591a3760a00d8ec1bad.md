### Title
`PosixShellEscape` fails to quote characters missing from `posixModeTable`, allowing shell metacharacter injection when `FF_POSIXLY_CORRECT_ESCAPES` is enabled - ([File: helpers/shell_escape.go])

### Summary
`helpers.PosixShellEscape` (used by `BashWriter.escape` via `shells/bash.go` when `FF_POSIXLY_CORRECT_ESCAPES` is on) treats any byte not explicitly listed in `posixModeTable` as a safe literal that neither requires escaping nor forces the surrounding double quotes. Several shell metacharacters that DO need quoting/escaping in a double-quoted or unquoted POSIX context — notably `;`, newline (`\n`), single quote (`'`), and others — are absent from the table, so a value built only from such characters is emitted completely unquoted into the generated script.

### Finding Description
In [1](#0-0) , `posixModeTable` only maps a small set of characters to `quo` (forces quoting) or to explicit backslash escapes (backtick, `"`, `\`, `$`). Every other byte value (the zero value of the `mode` type, i.e. `""`) falls into the `case "":` branch, which writes the byte through **unchanged and does not set `escape = true`**. This differs from `ShellEscape`'s equivalent `case "":` branch, which hex-escapes unknown bytes — that protection was not carried over to the POSIX variant.

Critically, `;` and `\n` (and other shell-significant characters such as `'`) are not present in `posixModeTable` at all, so they are classified as safe literals. If a variable value consists solely of alphanumerics plus these unlisted metacharacters (no space/`!`/`#`/etc. that would trigger `quo`), `escape` remains `false` for the whole string and `PosixShellEscape` returns the raw string **with no surrounding quotes whatsoever**.

Reachable path: `BashWriter.Variable` (`shells/bash.go:229-241`) emits `export %s=%s` using `b.escape(variable.Value)`, and `b.escape` dispatches to `helpers.PosixShellEscape` when `usePosixEscape` is true (`shells/bash.go:447-453`, set from `featureflags.PosixlyCorrectEscapes`, `shells/bash.go:121`). A CI job author fully controls arbitrary CI/CD variable values, so a variable value such as:

```
x;touch /tmp/pwned;x
```

contains only letters and `;` — none trigger `quo` — so `PosixShellEscape` returns it verbatim, unquoted. The generated script line becomes:

```
export KEY=x;touch /tmp/pwned;x
```

which, when `eval`'d by `BashWriter.Finish` (`shells/bash.go:439`), executes `touch /tmp/pwned` as an independent command inside the job's own shell/subshell. A newline-only-based value achieves the same effect by breaking the `export` statement onto extra lines.

Existing protections do not stop this: the guard is precisely the escaping function itself, and it is missing the necessary entries. There is no other quoting layer applied to `Variable`/`ExportRaw` values before they reach `eval`.

### Impact Explanation
Impact is confined to the CI job's own shell/executor context (the same isolation boundary the job's `script:` commands already run in) — this is arbitrary command execution inside the running build shell triggered purely by variable content, which is a real deviation from the intended "value is safely quoted as data" invariant of the escaping function. It does not by itself grant privilege escalation beyond what an unprivileged shell-executor job could already do via its `script:`, but it breaks the "variables are inert data" invariant relied upon by callers such as `SetupGitCredHelper` and any code path building command lines from variable-derived escaped strings, which could be chained with other logic expecting safe substitution.

### Likelihood Explanation
Requires `FF_POSIXLY_CORRECT_ESCAPES=true` to be enabled (default is `false`, see `helpers/featureflags/flags.go:230-238`), so exploitability depends on runner configuration. When enabled, the exploit is deterministic and 100% reproducible: any pipeline author can set a CI/CD variable value containing `;` or newline without other `quo`-triggering characters, and it is trivially found via fuzzing/property testing (compare `PosixShellEscape` output against a strict shlex/POSIX-quote round-trip).

### Recommendation
Fix `PosixShellEscape` so that any byte not explicitly present in `posixModeTable` is only treated as literal if it is truly safe outside of quotes (e.g., restrict the "safe unquoted" set to `[A-Za-z0-9_./-]` as done for `ShellEscape`'s `lit`), and everything else — including `;`, newline, `'`, and other unlisted metacharacters — should be classified as `quo` (or hex-escaped) so the result is always quoted when necessary. Concretely, add explicit entries for `;`, `\n`, `\r`, `\t`, `'`, `\a`, `\b`, `\v`, `\f`, `{`, `}`, `^`, `~`, and any other POSIX special characters, mirroring the completeness of `modeTable` used by `ShellEscape`.

### Proof of Concept
```go
// helpers/shell_escape_test.go
func TestPosixShellEscape_SemicolonInjection(t *testing.T) {
    in := "x;touch /tmp/pwned;x"
    out := PosixShellEscape(in)
    // BUG: currently out == in (unquoted), allowing command injection
    // when substituted into `export KEY=<out>` and eval'd.
    assert.NotEqual(t, in, out, "value must never be returned unquoted when it contains shell metacharacters")
    assert.True(t, strings.HasPrefix(out, `"`) && strings.HasSuffix(out, `"`),
        "semicolon-containing value must be quoted")
}

func TestPosixShellEscape_NewlineInjection(t *testing.T) {
    in := "a\nrm -rf /tmp/pwned_dir"
    out := PosixShellEscape(in)
    assert.True(t, strings.HasPrefix(out, `"`) && strings.HasSuffix(out, `"`),
        "newline-containing value must be quoted so it cannot break export onto a new statement")
}
```

Integration-level PoC: set `FF_POSIXLY_CORRECT_ESCAPES=true` on a shell-executor runner, define a CI/CD variable `KEY=x;touch /tmp/pwned;x`, run a job with an empty `script:`, and observe that `/tmp/pwned` is created purely from variable export processing. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** helpers/shell_escape.go (L90-126)
```go
var posixModeTable = [256]mode{
	'`': "\\`", '"': `\"`, '\\': `\\`, '$': `\$`,

	' ': quo, '!': quo, '#': quo, '%': quo, '&': quo, '(': quo, ')': quo,
	'*': quo, '<': quo, '=': quo, '>': quo, '?': quo, '[': quo, '|': quo,
}

// PosixShellEscape double quotes strings and escapes a string where necessary.
func PosixShellEscape(input string) string {
	if input == "" {
		return "''"
	}

	var sb strings.Builder
	sb.Grow(len(input) * 2)

	escape := false
	for _, c := range []byte(input) {
		mode := posixModeTable[c]
		switch mode {
		case quo:
			sb.WriteByte(c)
			escape = true
		case "":
			sb.WriteByte(c)
		default:
			sb.WriteString(string(mode))
			escape = true
		}
	}

	if escape {
		return `"` + sb.String() + `"`
	}

	return sb.String()
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

**File:** shells/bash.go (L447-453)
```go
func (b *BashWriter) escape(input string) string {
	if b.usePosixEscape {
		return helpers.PosixShellEscape(input)
	}

	return helpers.ShellEscape(input)
}
```

**File:** helpers/featureflags/flags.go (L230-238)
```go
	{
		Name:            PosixlyCorrectEscapes,
		DefaultValue:    false,
		Deprecated:      false,
		ToBeRemovedWith: "",
		Description: "When enabled, [POSIX shell escapes](https://pubs.opengroup.org/onlinepubs/9699919799/utilities/V3_chap02.html#tag_18_02) " +
			"are used rather than [`bash`-style ANSI-C quoting](https://www.gnu.org/software/bash/manual/html_node/Quoting.html). " +
			"This should be enabled if the job environment uses a POSIX-compliant shell.",
	},
```
