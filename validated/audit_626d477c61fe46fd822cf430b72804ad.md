## Analysis Confirmed: FF-Dependent Command Injection in `helpers/shell_escape.go`

I traced the code path from `spec.Variable` values through `BashWriter.Variable()` at [1](#0-0)  down into `BashWriter.escape()` at [2](#0-1) , which dispatches to either `helpers.ShellEscape` or `helpers.PosixShellEscape` based on `featureflags.PosixlyCorrectEscapes` [3](#0-2) .

The two escape tables are not equivalent for control characters:

- `modeTable` (used by `ShellEscape`) explicitly maps `\n`, `\t`, `\r`, `\a`, `\b`, `\v`, `\f` to ANSI-C escape sequences that force `escape = true` and cause the string to be wrapped in `$'...'` [4](#0-3) .
- `posixModeTable` (used by `PosixShellEscape`) has **no entries** for these control characters at all — only `` ` ``, `"`, `\`, `$` are escaped, and only a small punctuation set (space, `!#%&()*<=>?[|`) forces quoting [5](#0-4) . Any byte not in that table (including `\n`, `\t`, `\r`, and any other control byte) falls to the `case ""` branch, which writes it **literally with no quoting** [6](#0-5) .

Consequently, if a job-controlled variable value's entire content avoids the small posix quo/escape set but contains a raw newline (or tab, used as an IFS field separator instead of space), `PosixShellEscape` returns the value **completely unquoted**, while `ShellEscape` would have safely wrapped the identical value in `$'...'`.

### Title
FF_POSIXLY_CORRECT_ESCAPES causes unquoted-newline command injection in `PosixShellEscape` - (File: `helpers/shell_escape.go`)

### Summary
`PosixShellEscape`'s `posixModeTable` omits control characters (`\n`, `\t`, `\r`, etc.) from both its escape and quote sets, so a value containing only a newline plus characters outside the small posix quo/escape list is returned completely unquoted. When such a value comes from a job-controlled variable and is embedded via `BashWriter.Variable`/`escape()` into the generated script, the newline breaks out of the intended `export KEY=VALUE` statement and injects an arbitrary new shell command line — a behavior that does not occur with the default `ShellEscape` (ANSI-C) path, where control characters always force `$'...'` quoting.

### Finding Description
`BashWriter.Variable()` builds `export %s=%s` lines directly from `b.escape(variable.Value)` with no additional quoting [7](#0-6) . `variable.Value` originates from job/pipeline-controlled CI/CD variables. When `FF_POSIXLY_CORRECT_ESCAPES` is enabled, `escape()` calls `PosixShellEscape` [8](#0-7) .

`PosixShellEscape` only sets `escape = true` (which triggers wrapping the whole value in `"..."`) when a character is present from `posixModeTable`'s explicit backslash-escape set (`` ` " \ $ ``) or its quote set (`  ! # % & ( ) * < = > ? [ |`) [5](#0-4) . Any other byte — including `\n`, `\t`, `\r`, and other control characters — is written verbatim via the default `case ""` branch, and does **not** set `escape = true` [9](#0-8) . If no character in the whole value trips `escape = true`, the function returns the raw, completely unquoted string [10](#0-9) .

For example, the value `"\ntouch\t/tmp/pwned"` contains only a newline, letters, a tab, and `/` — none of which appear in `posixModeTable` — so `PosixShellEscape` returns it unchanged. Embedded into `export FOO=` + that value, the generated script becomes:
```
export FOO=
touch	/tmp/pwned
```
The second line executes as an independent shell command inside the job's build script. Under the default `ShellEscape` path, the same value's `\n` and `\t` are matched in `modeTable` (mapped to `\n`/`\t` escape sequences), forcing `escape = true` and wrapping the whole thing in `$'...'`, which stays a single safely-quoted token with no injection.

No existing check mitigates this: variable values are not otherwise sanitized before being written into the generated shell script, and the space/quo-character check in `PosixShellEscape` is not a substitute for a control-character check.

### Impact Explanation
An unprivileged pipeline author who can set a CI/CD job variable (including via `.gitlab-ci.yml` `variables:`, trigger API variables, or any variable value the job controls) can inject and execute arbitrary shell commands in the build script whenever the runner has `FF_POSIXLY_CORRECT_ESCAPES` enabled. Because the shell executor / bash-based executors run scripts with the same privileges as the rest of the job, this permits out-of-project-root file access, tampering with other steps of the job, or exfiltrating other jobs' cached secrets/tokens on shared runners — precisely the scoped "FF-dependent command injection enabling out-of-root file access" impact, dependent purely on the feature flag toggle rather than on any input-safety property.

### Likelihood Explanation
This requires only that the runner operator has enabled `FF_POSIXLY_CORRECT_ESCAPES` (a documented, supported configuration for POSIX-compliant shells like `dash`) and that the attacker can set any job variable value containing a raw newline plus content avoiding the dozen-or-so posix quo/escape characters and space (tabs work as word separators instead). Both conditions are easily satisfiable by any pipeline author; the bug is fully deterministic and repeatable — not a race condition or timing issue.

### Recommendation
Add all Bash/POSIX-significant control characters (`\n`, `\r`, `\t`, `\a`, `\b`, `\v`, `\f`) and any other non-printable byte to `posixModeTable` so that they either force quoting (`quo`) or are backslash/hex-escaped, mirroring the coverage `modeTable` already provides for `ShellEscape`. Alternatively, change `PosixShellEscape` to force `escape = true` whenever the input contains any byte with mode `""` that is a control character (byte < 0x20 or 0x7f) or is not a "safe" literal, rather than only reacting to the small quo list.

### Proof of Concept
Go unit test in `helpers/shell_escape_test.go` (differential fuzz idea):
```go
func TestPosixShellEscape_ControlCharInjection(t *testing.T) {
    payload := "\ntouch\t/tmp/pwned" // no chars in posixModeTable's quo/escape sets
    got := PosixShellEscape(payload)
    // BUG: returned value is unquoted/unescaped raw string
    assert.NotEqual(t, payload, got, "PosixShellEscape must not return raw unquoted control-char payloads")

    script := fmt.Sprintf("export FOO=%s\necho done\n", got)
    out, err := exec.Command("bash", "-c", script).CombinedOutput()
    require.NoError(t, err)
    _, statErr := os.Stat("/tmp/pwned")
    assert.True(t, os.IsNotExist(statErr), "command injection occurred: /tmp/pwned was created")
}
```
A companion differential fuzz test (`go test -fuzz`) can feed a corpus of shell metacharacters, backticks, `$()`, newlines/tabs, NUL, and high-byte UTF-8 through both `ShellEscape` and `PosixShellEscape`, execute each result via `bash -c "export V=<escaped>; echo $V" ` and assert that (a) neither produces command execution beyond the intended `export`, and (b) both preserve the original byte value verbatim in `$V`. The newline/tab-only corpus entries will fail this assertion only for `PosixShellEscape`, confirming the FF-dependent divergence.

### Citations

**File:** shells/bash.go (L119-121)
```go
		checkForErrors:    build.IsFeatureFlagOn(featureflags.EnableBashExitCodeCheck),
		useLegacyBashEval: build.IsFeatureFlagOn(featureflags.UseLegacyBashEval),
		usePosixEscape:    build.IsFeatureFlagOn(featureflags.PosixlyCorrectEscapes),
```

**File:** shells/bash.go (L229-240)
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

**File:** helpers/shell_escape.go (L26-48)
```go
var modeTable = [256]mode{
	'\a': `\a`, '\b': `\b`, '\t': `\t`, '\n': `\n`, '\v': `\v`, '\f': `\f`,
	'\r': `\r`, '\'': `\'`, '\\': `\\`,

	',': lit, '-': lit, '.': lit, '/': lit,
	'0': lit, '1': lit, '2': lit, '3': lit, '4': lit, '5': lit, '6': lit,
	'7': lit, '8': lit, '9': lit,

	'@': lit, 'A': lit, 'B': lit, 'C': lit, 'D': lit, 'E': lit, 'F': lit,
	'G': lit, 'H': lit, 'I': lit, 'J': lit, 'K': lit, 'L': lit, 'M': lit,
	'N': lit, 'O': lit, 'P': lit, 'Q': lit, 'R': lit, 'S': lit, 'T': lit,
	'U': lit, 'V': lit, 'W': lit, 'X': lit, 'Y': lit, 'Z': lit,

	'_': lit, 'a': lit, 'b': lit, 'c': lit, 'd': lit, 'e': lit, 'f': lit,
	'g': lit, 'h': lit, 'i': lit, 'j': lit, 'k': lit, 'l': lit, 'm': lit,
	'n': lit, 'o': lit, 'p': lit, 'q': lit, 'r': lit, 's': lit, 't': lit,
	'u': lit, 'v': lit, 'w': lit, 'x': lit, 'y': lit, 'z': lit,

	' ': quo, '!': quo, '"': quo, '#': quo, '$': quo, '%': quo, '&': quo,
	'(': quo, ')': quo, '*': quo, '+': quo, ':': quo, ';': quo, '<': quo,
	'=': quo, '>': quo, '?': quo, '[': quo, ']': quo, '^': quo, '`': quo,
	'{': quo, '|': quo, '}': quo, '~': quo,
}
```

**File:** helpers/shell_escape.go (L90-95)
```go
var posixModeTable = [256]mode{
	'`': "\\`", '"': `\"`, '\\': `\\`, '$': `\$`,

	' ': quo, '!': quo, '#': quo, '%': quo, '&': quo, '(': quo, ')': quo,
	'*': quo, '<': quo, '=': quo, '>': quo, '?': quo, '[': quo, '|': quo,
}
```

**File:** helpers/shell_escape.go (L107-119)
```go
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
```

**File:** helpers/shell_escape.go (L121-125)
```go
	if escape {
		return `"` + sb.String() + `"`
	}

	return sb.String()
```
