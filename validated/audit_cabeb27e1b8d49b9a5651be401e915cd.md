### Title
Trace section preview embeds raw, unsanitized multiline command bytes, enabling forged `section_start`/`section_end` trace markers - ([File: functions/script_legacy/internal/trace_section.go])

### Summary
`TraceSectionWriter.writeSectionStart` escapes the **entire** multiline command (not just a truncated first line) and embeds it as the "preview" text after the real `section_start:...` marker. Both escaping paths (`EscapeForAnsiC` wrapped in `$'...'` and `EscapeForPosix`) fail to prevent the shell from reconstructing raw newlines, carriage-returns and ESC sequences at execution time, so a job-controlled command containing crafted `section_end:`/`section_start:` text is reproduced byte-for-byte in the job log and is parsed by GitLab as real structural trace markers.

### Finding Description
`ProcessCommand` routes any multiline command into trace sections when `TraceSections=true`: [1](#0-0) 

`WriteSection` -> `writeSectionStart` builds the printf line using the **full, untruncated** `command` string (unlike `CommandFormatter.getDisplayCommand`, which is only used for the non-trace-section log line): [2](#0-1) 

The command is passed through `w.escape(...)`: [3](#0-2) 

For the ANSI-C path, `EscapeForAnsiC` converts control bytes (`\n`, `\r`, `\x1B`, etc.) into their **textual backslash-escape representation** (e.g. real newline byte -> the two characters `\` `n`; real ESC byte -> `\x1b`): [4](#0-3) 

That output is then wrapped in `$'...'` ANSI-C quoting: [5](#0-4) 

`$'...'` is interpreted by bash/sh at **parse time**: it converts `\n`, `\r`, `\t`, `\xHH`, etc. back into the literal raw bytes before the resulting string is ever passed to `printf`. So the "escaping" performed by `EscapeForAnsiC` is completely undone by the very quoting mechanism that wraps it — the printf argument bash actually constructs contains the original raw newline/CR/ESC bytes of the job-controlled command, unmodified.

For the POSIX path, it's even more direct: `EscapeForPosix` never touches `\n` at all — its `switch` only escapes `` ` ``, `"`, `\`, `$`, and quotes a handful of punctuation/space characters; newline falls through to the `default` branch and is copied through literally: [6](#0-5) 

In both modes, the final printf argument that reaches the job log's stdout contains real newline/CR/ESC bytes taken straight from the attacker-controlled multiline command. Since GitLab's trace/log parser recognizes section boundaries by scanning raw trace text for lines beginning with `section_start:`/`section_end:` (with `\r\e[0K` framing) — the same textual format `writeSectionStart`/`writeSectionEnd` themselves emit — a command whose text contains a crafted embedded `section_end:<ts>:section_X\r\033[0K...section_start:<ts>:section_Y[collapsed=true]\r\033[0K` sequence will, once printed via this single "preview" `printf`, produce multiple lines in the log output that are indistinguishable from genuine section markers.

Existing controls (masking of secrets, allowed-image checks, path validation) do not apply to raw stdout text emitted by a job's own command, and there is no sanitization step that strips or neutralizes literal `section_start:`/`section_end:` prefixes, embedded `\r`, or ESC sequences from job-authored command text before it is echoed as a section preview.

### Impact Explanation
An unprivileged pipeline author can craft a `script:` entry (a multiline command) whose text embeds fake `section_end:`/`section_start:` marker lines and CR+`\e[0K` sequences. When `TraceSections=true` (a common Runner feature-flag/setting, e.g. `FF_SCRIPT_SECTIONS`), the crafted text is reproduced verbatim in the trace output as if it were a genuine collapsed-section boundary. This lets the attacker prematurely close a real section and/or open a fake collapsed section around subsequent job output, hiding or obscuring malicious commands (e.g. secret exfiltration commands) from a reviewer skimming the collapsed/expanded trace UI in GitLab — matching the scoped impact of trace/log forging.

### Likelihood Explanation
- Requires only `TraceSections=true` (server/runner configuration commonly enabled) and a job author able to supply a multiline `script:` command — both are attacker-controlled, unprivileged conditions.
- No special runner/executor privileges are needed; this is pure text manipulation of the generated shell script that the Runner itself writes into the job's stdout.
- The bug is deterministic and repeatable: any job with the crafted first-line/embedded content triggers it every run.

### Recommendation
Sanitize/neutralize the command preview before embedding it in the `section_start` printf argument:
- Strip or escape literal `\r` and ESC (`\x1B`) bytes into a form that survives the *outer* shell quoting (i.e., do not rely on `$'...'`/`EscapeForAnsiC` semantics being undone by ANSI-C quoting — either avoid `$'...'` entirely and manually double-hex-escape, or perform the neutralization after quote expansion is accounted for).
- Additionally, restrict the section preview to a single sanitized line only (as `CommandFormatter.getDisplayCommand` does via `getFirstLine`), and explicitly strip any substring matching `^section_(start|end):` patterns or embedded `\r`/ESC bytes from that line before emission, so job-controlled text can never be mistaken for a genuine trace-section marker line by the GitLab trace parser.

### Proof of Concept
```go
func TestTraceSectionWriter_CannotForgeSectionMarkers(t *testing.T) {
    w := NewTraceSectionWriter(false, false /* posixMode */)
    var buf strings.Builder

    malicious := "echo start\n" +
        "section_end:1700000000:section_script_step_0\r\033[0K" +
        "section_start:1700000000:section_fake[collapsed=true]\r\033[0K" +
        "echo hidden-exfil-command\n" +
        "done"

    w.WriteSection(&buf, 0, malicious)
    out := buf.String()

    // Extract the literal argument bash would pass to printf once $'...' is expanded.
    // Assert the *rendered* trace text (simulate bash ANSI-C expansion) does NOT
    // contain a raw, unescaped "section_end:" / "section_start:" line other than
    // the two legitimate ones written by writeSectionStart/writeSectionEnd.
    expanded := simulateBashAnsiCExpansion(out) // helper implementing $'...' rules

    markerLines := extractLinesStartingWith(expanded, "section_start:", "section_end:")
    assert.Len(t, markerLines, 2, "only the genuine section_start and section_end markers should be present")
}
```
Expected (current buggy) result: `markerLines` contains 4 entries (2 genuine + 2 forged), proving the crafted command text is reconstructed as literal structural markers in the trace stream. After the fix, only the 2 genuine markers should appear, with the malicious `section_end:`/`section_start:` text either stripped or rendered inert (e.g. as escaped/non-executable text).

### Citations

**File:** functions/script_legacy/internal/command_processor.go (L38-47)
```go
	if p.shouldUseTraceSection(command) {
		p.sectionWriter.WriteSection(buf, index, command)
	} else {
		p.writeNormalCommand(buf, command)
	}
}

func (p *CommandProcessor) shouldUseTraceSection(command string) bool {
	return p.traceSections && isMultiline(command)
}
```

**File:** functions/script_legacy/internal/trace_section.go (L50-62)
```go
// writeSectionStart writes the section_start marker with command preview.
// Format: section_start:TIMESTAMP:section_NAME[options]\r\e[0K\e[32;1m$ COMMAND\e[0;m
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

**File:** functions/script_legacy/internal/escape.go (L14-50)
```go
func EscapeForAnsiC(s string) string {
	var buf strings.Builder

	for i := 0; i < len(s); i++ {
		c := s[i]
		switch c {
		case '\\':
			buf.WriteString("\\\\")
		case '\'':
			buf.WriteString("\\'")
		case '\n':
			buf.WriteString("\\n")
		case '\r':
			buf.WriteString("\\r")
		case '\t':
			buf.WriteString("\\t")
		case '\a':
			buf.WriteString("\\a")
		case '\b':
			buf.WriteString("\\b")
		case '\f':
			buf.WriteString("\\f")
		case '\v':
			buf.WriteString("\\v")
		default:
			// Hex-escape control characters (0x00-0x1F, 0x7F) and non-ASCII (>0x7F)
			// This prevents ANSI escape sequences (ESC = 0x1B) from manipulating terminal
			if c < 0x20 || c == 0x7F || c > 0x7F {
				fmt.Fprintf(&buf, "\\x%c%c", hextable[c>>4], hextable[c&0x0f])
			} else {
				buf.WriteByte(c)
			}
		}
	}

	return buf.String()
}
```

**File:** functions/script_legacy/internal/escape.go (L63-75)
```go
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
```
