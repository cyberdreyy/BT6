### Title
`ExportRaw` variable-name quoting lets a `=` in an attacker-controlled key redefine which variable is set - (File: shells/bash.go)

### Summary
`BashWriter.ExportRaw` builds `export <escaped-name>=<value>` by quoting the *name* with the general-purpose shell-string escaper (`b.escape`, i.e. `helpers.ShellEscape`/`helpers.PosixShellEscape`) instead of validating it as a POSIX shell identifier. Both escapers treat `=` as a character that merely needs *quoting*, not one that must be rejected/escaped out of an identifier, so a name containing `=` produces a single concatenated word after quote removal that the `export` builtin re-splits on its own first `=`, silently binding the value to a shorter/different variable name than the one intended.

### Finding Description
`ExportRaw` is:
```go
func (b *BashWriter) ExportRaw(name, value string) {
	b.Linef(`export %s=%s`, b.escape(name), doubleQuote(value))
}
``` [1](#0-0) 

`b.escape` is either `helpers.ShellEscape` (ANSI-C `$'...'` quoting) or `helpers.PosixShellEscape` (`"..."` quoting) depending on the `PosixlyCorrectEscapes` feature flag. [2](#0-1) 

Both quoting tables classify `=` as a "quote-only" character (`quo`), meaning the character is preserved verbatim inside the quotes and only triggers the addition of surrounding quote marks — it is never rejected or escaped out of the identifier:
- `ShellEscape`: `'=': quo` inside the `modeTable` [3](#0-2) 
- `PosixShellEscape`: `'=': quo` inside `posixModeTable` [4](#0-3) 

If `name` is e.g. `A=B`, `ShellEscape` returns `$'A=B'`, so the generated line is:
```
export $'A=B'=value
```
Bash performs quote removal and word concatenation on this single token before the `export` builtin ever runs its own `NAME=value` parsing, producing the literal argument string `A=B=value`. The `export` builtin then splits on the *first* `=` in that literal string, so the effective result is:
```
A → "B=value"
```
instead of the intended variable `A=B` holding `value`. The same collapsing happens with `PosixShellEscape` (`"A=B"=value` → literal `A=B=value` after quote removal). This is the parser-differences described in the question: the reference the script *generates* (`$'A=B'=value` / `"A=B"=value`) does not bind to the variable name the caller intended (`A=B`); it silently truncates to `A` and prepends attacker-influenced text (`B=`) to the exported value.

`isValidDotEnvKey` exists elsewhere and is applied only when rendering the dotenv heredoc file via `DotEnvEscape` [5](#0-4) [6](#0-5) 
but `ExportRaw` itself performs no equivalent identifier validation before quoting — it assumes `name` is already a safe shell identifier, which is not guaranteed for every caller.

Because of tool-iteration limits I was not able to fully trace every call site of `ExportRaw` (grep showed 2 call sites in `shells/abstract.go`) to confirm precisely which upstream attacker-controlled data (dotenv artifact keys, CI/CD step outputs, job variable keys, etc.) can reach this `name` argument without prior strict `^[A-Za-z_][A-Za-z0-9_]*$` validation. If any such caller passes an externally-supplied key straight to `ExportRaw` without validating it against that pattern first, the confusion described above is directly reachable.

### Impact Explanation
An attacker who can control a variable *name/key* that ends up passed to `ExportRaw` (rather than a value) can cause the generated script to bind the exported value to a different, truncated variable name than intended, and can prepend attacker-chosen text to that variable's value. Depending on which variable name collision occurs, this could redefine or corrupt an existing environment variable used later in the job (e.g., a variable whose name is a prefix of the attacker's crafted key), leading to unexpected script behavior, environment corruption, or in combination with a downstream trusting consumer of that variable, secret exposure or command injection. Impact is contingent on finding a live call site that feeds an unvalidated, attacker-controlled string into `ExportRaw`'s `name` parameter.

### Likelihood Explanation
The root-cause quoting flaw in `ExportRaw`/`helpers.ShellEscape`/`helpers.PosixShellEscape` is unconditionally present and trivially demonstrable with any `name` containing `=`. Exploitability at the job level depends on whether GitLab Runner (or GitLab's variable-key validation) ever passes an unsanitized key to this function; standard project/pipeline CI/CD variable keys are validated by GitLab against an identifier pattern before reaching the Runner, which would block this in the common case, but this could not be fully confirmed for every `ExportRaw` caller (e.g., dotenv-report-derived variables, step-metadata-derived keys) within the available investigation.

### Recommendation
Validate `name` in `ExportRaw` (and in `BashWriter.Variable`) against a strict POSIX shell identifier pattern (`^[A-Za-z_][A-Za-z0-9_]*$`) before use, rejecting or skipping variables with invalid names instead of shell-escaping them; escaping should only ever be applied to the value, never relied upon to make an arbitrary string safe as a bare (unquoted-position) identifier token in an `export NAME=value` statement.

### Proof of Concept
Go unit test in `shells/bash_test.go` (new):
```go
func TestBashWriter_ExportRaw_NameWithEquals(t *testing.T) {
    w := &BashWriter{}
    w.ExportRaw("A=B", "value")
    script := w.String()
    // Assert the generated script does NOT collapse to `export A=B=value`
    // i.e. it must not allow `A` to be set instead of a variable literally named "A=B".
    assert.NotContains(t, script, "export $'A=B'=value")
    assert.NotContains(t, script, `export "A=B"=value`)
}
```
Expected current (buggy) behavior: assertion fails because the generated line is `export $'A=B'=value`, and running it in bash sets `A="B=value"` rather than failing or setting a variable literally named `A=B`. Confirm via a bash execution PoC:
```bash
eval "export \$'A=B'=value"; echo "$A"   # prints "B=value" — variable A was set, not "A=B"
```

### Citations

**File:** shells/bash.go (L243-245)
```go
func (b *BashWriter) ExportRaw(name, value string) {
	b.Linef(`export %s=%s`, b.escape(name), doubleQuote(value))
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

**File:** helpers/shell_escape.go (L44-48)
```go
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

**File:** helpers/shell_escape.go (L128-134)
```go
// isValidDotEnvKey checks if a key is valid for a .env file
// (alphanumeric or underscores, starting with a letter or underscore).
func isValidDotEnvKey(key string) bool {
	validKeyPattern := `^[A-Za-z_][A-Za-z0-9_]*$`
	matched, _ := regexp.MatchString(validKeyPattern, key)
	return matched
}
```

**File:** helpers/shell_escape.go (L146-167)
```go
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
