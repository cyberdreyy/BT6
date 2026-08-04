### Title
`PosixShellEscape`'s `posixModeTable` omits `;` (and other bash-quoted metacharacters), allowing command-separator injection when `FF_POSIXLY_CORRECT_ESCAPES` is enabled - ([File: helpers/shell_escape.go])

### Summary
`ShellEscape` (bash mode) and `PosixShellEscape` (POSIX-sh mode) use two independently maintained tables, `modeTable` and `posixModeTable`, to decide which bytes require quoting. `posixModeTable` is missing several characters that `modeTable` treats as `quo` (must-quote) — most critically `;`, the shell command separator — and also `:`, `^`, `~`, `{`, `}`, `]`. When a value contains only characters absent from `posixModeTable`'s quoting set (including `;`), `PosixShellEscape` returns the value completely unquoted, which allows an attacker-controlled job variable to inject a second shell statement into the generated POSIX-sh script.

### Finding Description
`helpers/shell_escape.go` defines two tables: [1](#0-0) [2](#0-1) 

`modeTable` (used by `ShellEscape`, bash mode) marks `;` as `quo`. `posixModeTable` (used by `PosixShellEscape`) does **not** list `;` at all, so it falls into the default `""` case, which is treated as "no escaping needed" and passed through literally: [3](#0-2) 

Crucially, `PosixShellEscape` only wraps the result in double quotes if `escape` was set to `true` by *some* character in the input. If a value contains `;` but none of the characters in `posixModeTable`'s `quo`/escape set (space, `!`, `#`, `%`, `&`, `(`, `)`, `*`, `<`, `=`, `>`, `?`, `[`, `|`, backtick, `"`, `\`, `$`), the function returns the raw, completely unquoted string — semicolon included.

`BashWriter.escape` selects between the two functions purely based on a build-time feature flag, not based on any content inspection: [4](#0-3) 

This escape function is used directly to render job variables into the generated shell script: [5](#0-4) 

**Exploit flow**: a pipeline author sets a CI/CD job variable value such as `x;whoami` (no spaces, no other reserved characters). When `FF_POSIXLY_CORRECT_ESCAPES` is enabled, `BashWriter.Variable` emits `export KEY=x;whoami` verbatim into the job script with no quoting, because none of the characters in `x;whoami` trip `posixModeTable`'s escape logic. When this script is executed via `sh -c`/`eval` in `job_unix.go`'s `exec.Cmd`, the shell parses `;` as a statement separator, executing `whoami` (or any other space-free single command/subshell payload) as an independent command outside the intended `export` statement.

This directly violates the stated invariant: every character `modeTable` classifies as requiring quoting must be safely handled by `posixModeTable` too. `;` is such a character, and it is not.

### Impact Explanation
An attacker who can set a job/pipeline variable value can inject and execute an arbitrary additional shell statement in the runner's build container/host when `FF_POSIXLY_CORRECT_ESCAPES` is enabled, without needing to use shell metacharacters that are otherwise blocked. This is command execution beyond the authored payload inside the job's own shell context — matching the scoped impact ("runner-side command execution / unintended commands outside authored payload").

### Likelihood Explanation
- Precondition: `FeatureFlags.PosixlyCorrectEscapes` must be enabled on the runner (opt-in flag), which is an allowed precondition per the question.
- The attacker only needs to control a variable value consumed via `BashWriter.Variable`/`escape` (standard CI/CD variable, which pipeline authors control) containing a bare `;` and no other character from `posixModeTable`'s quoting set — an easily satisfiable, repeatable condition (e.g. `x;whoami`, `x;id`, `x;curl${IFS}evil` variants avoiding `$`/`(` etc. can be shaped further, but the bare `;` case alone is a complete break already).
- No existing check (masking, variable sanitization, allowed-image checks) inspects variable values for shell metacharacters before this escaping step; escaping is the only defense, and it is broken for this specific character class.

### Recommendation
Align `posixModeTable` with `modeTable`'s quoting decisions for all POSIX shell metacharacters that terminate or separate commands/words, at minimum adding `;`, and also reconcile `:`, `^`, `~`, `{`, `}`, `]` for defense-in-depth consistency. Ideally, generate both tables from a single shared "characters requiring quoting" set with documented per-shell exceptions, and add a test asserting that for every byte 0–255, if `modeTable` marks it `quo`/escape, `posixModeTable` must also mark it `quo`/escape (or vice versa), so the two tables can never silently diverge again.

### Proof of Concept
Go unit test in `helpers/shell_escape_test.go`:
```go
func TestPosixShellEscape_SemicolonNotQuoted(t *testing.T) {
    out := PosixShellEscape("x;whoami")
    // BUG: currently returns "x;whoami" unquoted
    assert.NotEqual(t, "x;whoami", out, "PosixShellEscape must quote ';' to prevent command separation")
}

func TestShellEscapeTablesAgreeOnQuoting(t *testing.T) {
    for c := 0; c < 256; c++ {
        bashNeedsQuote := modeTable[c] != "" && modeTable[c] != lit
        posixNeedsQuote := posixModeTable[c] != ""
        if bashNeedsQuote {
            assert.Truef(t, posixNeedsQuote,
                "byte %q required quoting in bash mode but not in posix mode", byte(c))
        }
    }
}
```
Integration PoC: generate a bash-shell script via `BashShell.GenerateScript` with `FF_POSIXLY_CORRECT_ESCAPES=true` and a job variable `INJECT=x;touch /tmp/pwned`, execute the resulting script with `sh`, and assert `/tmp/pwned` is created — demonstrating the exported variable statement was split into two commands.

### Citations

**File:** helpers/shell_escape.go (L44-47)
```go
	' ': quo, '!': quo, '"': quo, '#': quo, '$': quo, '%': quo, '&': quo,
	'(': quo, ')': quo, '*': quo, '+': quo, ':': quo, ';': quo, '<': quo,
	'=': quo, '>': quo, '?': quo, '[': quo, ']': quo, '^': quo, '`': quo,
	'{': quo, '|': quo, '}': quo, '~': quo,
```

**File:** helpers/shell_escape.go (L90-95)
```go
var posixModeTable = [256]mode{
	'`': "\\`", '"': `\"`, '\\': `\\`, '$': `\$`,

	' ': quo, '!': quo, '#': quo, '%': quo, '&': quo, '(': quo, ')': quo,
	'*': quo, '<': quo, '=': quo, '>': quo, '?': quo, '[': quo, '|': quo,
}
```

**File:** helpers/shell_escape.go (L107-126)
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
