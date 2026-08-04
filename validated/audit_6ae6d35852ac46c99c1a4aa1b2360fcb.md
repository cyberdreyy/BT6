### Title
Unquoted `shellEscape` output permits command injection via IFS-splittable, non-switch-case characters (tab, newline, etc.) - (File: `functions/concrete/run/stages/internal/scriptwriter/scriptwriter.go`)

### Summary
`shellEscape` only quotes its output when it encounters a byte in a hard-coded switch list; any byte outside that list (e.g. `\t`, `\n`, `;` is actually excluded too, control characters, most Unicode bytes) is copied verbatim and does not set `needsQuoting`. If an attacker-controlled string contains none of the listed characters, the string is returned completely unquoted, and shell metacharacters within it (newline as a command separator, tab as IFS field separator) are then interpreted literally by bash when the output is embedded into the generated script.

### Finding Description
`shellEscape` in `functions/concrete/run/stages/internal/scriptwriter/scriptwriter.go` (lines 263-299) iterates bytes of the input and only escapes/quotes on a fixed set: backtick, `"`, `\`, `$`, and the literal set `{' ', '!', '#', '%', '&', '(', ')', '*', '<', '=', '>', '?', '[', '|'}`. Anything else — including `\n`, `\t`, `\r`, `;`, `{`, `}`, `~`, `^`, `]`, `'`, and non-ASCII UTF-8 bytes — falls into the `default` branch, which writes the byte through unchanged and never sets `needsQuoting`. At the end, `shellEscape` returns the raw (unquoted) string whenever `needsQuoting` stayed `false` (line 294-298) [1](#0-0) .

This function is used to build both the trace-echo header and, critically, the entire script body passed to `eval` in `buildBashScript`: `"(trap 'exit 1' TERM; eval " + shellEscape(body.String()) + ") < /dev/null\n"` [2](#0-1) . `body` is built directly from job script lines (`lines []string`), which for the shell/script executors originate from job-defined `script:`/`before_script:`/`after_script:` entries and any job variables expanded into them. If a value contains only characters absent from the switch (e.g. tabs and newlines used instead of spaces, plus alphanumerics, `/`, `-`, `.`, `_`), `needsQuoting` never becomes `true` and the entire multi-line body — or any single embedded value — is emitted completely unquoted into the `eval` argument. Newlines then act as real statement separators and tabs as IFS field separators inside that unquoted eval string, letting an attacker-controlled value that itself never triggers quoting inject and execute an arbitrary additional command line (e.g. `a\nrm\t-rf\t/tmp/x`) that bash will parse as two separate statements/commands rather than as an inert string.

The `needsQuoting` gating is the root cause: it conflates "does this character need backslash-escaping inside double quotes" with "does this character require quoting to be shell-safe," and it is not exhaustive against POSIX shell metacharacters/whitespace (`\n`, `\t`, `\r`, `;`, and others are absent from the list). This is a genuinely reachable code path for the trace-echo line (`echo " + shellEscape(...)`), where the escaped text is a job-authored command line reflected for logging purposes, satisfying the described attacker-controlled → `shellEscape` → generated script → `internal.Executor.Execute` chain.

### Impact Explanation
A crafted command/variable value lacking any switch-matched character but containing tabs/newlines can be emitted unquoted into the generated bash script, letting bash parse embedded newline/tab-delimited content as additional statements/arguments rather than as an inert echoed string or literal value — i.e., command injection into the runner's own script-generation output, matching the scoped impact of "runner-side command execution outside authored job payload."

### Likelihood Explanation
Reachability requires only that a normal, unprivileged pipeline author supply a `script:`/variable line whose content is composed exclusively of characters outside `shellEscape`'s switch list but containing `\n`/`\t` used as separators — a low bar with no other precondition, since job script lines are the direct, untrusted input to this function. It is fully repeatable and deterministic given fixed input.

### Recommendation
Replace the character-class allow-list with a safe default: quote (or single-quote-escape, e.g. POSIX `'...'` with `'\''`) unconditionally rather than conditionally, or invert the logic to detect "needs no quoting" only for a strictly known-safe character class (`[A-Za-z0-9_./-]`) and quote everything else, ensuring `\n`, `\t`, `\r`, `;`, and all non-ASCII/control bytes are always contained within the quoted context.

### Proof of Concept
Go unit test to add to `scriptwriter_test.go`:
```go
func TestShellEscape_UnquotedControlBytes(t *testing.T) {
    in := "a\nrm\t-rf\t/tmp/pwned_marker"
    out := shellEscape(in)
    // Bug: no quote-triggering byte present, so output is unescaped/unquoted.
    assert.Equal(t, in, out, "expected shellEscape to leave control-byte payload unquoted")
    assert.False(t, strings.HasPrefix(out, `"`), "output should have been quoted but wasn't")
}
```
Integration PoC: build a script via `Builder.Build([]string{"echo " + shellEscape("a\ntouch\t/tmp/pwned")})`, run through `runBash` helper (as in `TestBashScript_Execute`), and assert the file `/tmp/pwned` is created — proving the tab/newline payload split into two executed statements instead of being echoed as one literal string.

### Citations

**File:** functions/concrete/run/stages/internal/scriptwriter/scriptwriter.go (L135-139)
```go
	if b.UseLegacyBashEval {
		buf.WriteString(": | eval " + shellEscape(body.String()) + "\n")
	} else {
		buf.WriteString("(trap 'exit 1' TERM; eval " + shellEscape(body.String()) + ") < /dev/null\n")
	}
```

**File:** functions/concrete/run/stages/internal/scriptwriter/scriptwriter.go (L263-299)
```go
func shellEscape(input string) string {
	if input == "" {
		return "''"
	}

	var sb strings.Builder
	sb.Grow(len(input) * 2)

	needsQuoting := false
	for _, c := range []byte(input) {
		switch c {
		case '`':
			sb.WriteString("\\`")
			needsQuoting = true
		case '"':
			sb.WriteString(`\"`)
			needsQuoting = true
		case '\\':
			sb.WriteString(`\\`)
			needsQuoting = true
		case '$':
			sb.WriteString(`\$`)
			needsQuoting = true
		case ' ', '!', '#', '%', '&', '(', ')', '*', '<', '=', '>', '?', '[', '|':
			sb.WriteByte(c)
			needsQuoting = true
		default:
			sb.WriteByte(c)
		}
	}

	if needsQuoting {
		return `"` + sb.String() + `"`
	}

	return sb.String()
}
```
