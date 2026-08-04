### Title
`EscapeForPosix` fails to hex-escape control characters, permitting raw ANSI/terminal escape injection into trace section previews - (File: functions/script_legacy/internal/escape.go)

### Summary
When `FF_POSIXLY_CORRECT_ESCAPES` is enabled, `TraceSectionWriter.escape` routes the section preview command through `EscapeForPosix` instead of `EscapeForAnsiC`. Unlike `EscapeForAnsiC`, `EscapeForPosix` only escapes backtick, double-quote, backslash, dollar, and a handful of shell metacharacters — it leaves bytes 0x00–0x1F (including ESC `0x1b` and BEL `0x07`) and `0x7F` completely untouched, so they pass through into the emitted `printf` command literally.

### Finding Description
`writeSectionStart` (functions/script_legacy/internal/trace_section.go:52-62) builds the section preview by wrapping the raw job command with ANSI color codes and calling `w.escape(...)`: [1](#0-0) 

`escape` dispatches to `EscapeForPosix` when `posixMode` (i.e. `config.PosixEscape`, driven by the feature flag) is true: [2](#0-1) 

`EscapeForPosix` only special-cases a small set of characters and otherwise copies bytes through unchanged: [3](#0-2) 

By contrast, `EscapeForAnsiC`'s own doc comment states the explicit security purpose of hex-escaping control bytes — "to prevent terminal manipulation" — and implements it: [4](#0-3) 

The `command` value passed into `WriteSection`/`writeSectionStart` originates directly from the job's CI script lines (`ProcessCommand` receives each script line and, for multiline commands, forwards it into `sectionWriter.WriteSection`): [5](#0-4) 

Because a GitLab CI author fully controls the text of `script:` lines, an attacker can craft a multiline command whose text contains raw control bytes such as `\x1b[2K\x1b[1A` (cursor-up/clear-line) or `\x1b[8m` (conceal text). Since the double-quoting characters (`` ` ``, `"`, `\`, `$`) are escaped, the attacker cannot break out of the quoted `printf` string, but the ESC/BEL bytes themselves remain literal inside the double-quoted argument and are printed verbatim by `printf '%s\n' "...`. When this trace output is streamed to and rendered by a terminal-emulating log viewer, the injected escape sequences can move the cursor, clear/overwrite previously displayed lines, or conceal text — i.e., spoof or hide portions of the job's own trace output.

The equivalent upstream helper `PosixShellEscape` in `helpers/shell_escape.go` has the identical gap (only escapes a fixed set of punctuation, no control-byte handling), confirming this is not an isolated bug in the local re-implementation but matches the intended behavior being replicated: [6](#0-5) 

No other check in this pipeline strips or validates control bytes before this point — `ProcessCommand` only trims whitespace and checks for emptiness, and `CommandFormatter.FormatLogLine` has the same posix-vs-ansiC asymmetry for the non-section echo path.

### Impact Explanation
An attacker who authors a CI job (with `FF_POSIXLY_CORRECT_ESCAPES=true` set, either by the attacker themselves if they control runner config exposure, or set at the runner/project level) can inject raw terminal control sequences into the `section_start` preview line of the trace log. This allows manipulating the rendered job log in terminal-based log viewers/consumers — e.g., hiding or overwriting prior log lines — to make malicious command output less visible to reviewers inspecting CI logs. Impact is limited to trace-log rendering/spoofing; it does not by itself escape the job sandbox or leak secrets from other jobs.

### Likelihood Explanation
Requires `FF_POSIXLY_CORRECT_ESCAPES` to be enabled (an opt-in feature flag) and `traceSections`/multiline command usage so `writeSectionStart` is invoked. Given that flag is enabled, any pipeline author can trivially craft a script line containing raw ESC bytes (e.g., via `printf` in YAML with literal bytes, or embedding them through variable expansion at script-authoring time) — the exploit is deterministic and fully reproducible.

### Recommendation
Make `EscapeForPosix` (and its runtime counterpart `PosixShellEscape` in `helpers/shell_escape.go`) hex-escape control characters (0x00–0x1F, 0x7F) the same way `EscapeForAnsiC` does, e.g. by adding a default case that emits `\x%02x` for those byte ranges instead of passing them through unchanged, while preserving double-quote-safe semantics (since `\xNN` is not interpreted inside POSIX double quotes, an alternate approach such as switching to `$'...'` ANSI-C quoting for any string containing control bytes, or stripping/hex-representing them before insertion, is required).

### Proof of Concept
```go
func FuzzEscapeForPosixNoRawControlBytes(f *testing.F) {
    f.Add("\x1b[2K\x1b[1Ahello")
    f.Add("\x07\x00\x7f")
    f.Fuzz(func(t *testing.T, s string) {
        out := EscapeForPosix(s)
        for i := 0; i < len(out); i++ {
            c := out[i]
            if c < 0x20 || c == 0x7f {
                t.Fatalf("raw control byte 0x%02x leaked through EscapeForPosix for input %q: %q", c, s, out)
            }
        }
    })
}
```
Expected: fails on current implementation (e.g. input `"\x1b[2K"` returns `"\x1b[2K"` unchanged, embedding a literal ESC), demonstrating the gap versus `EscapeForAnsiC`, which would hex-escape it to `\x1b`.

### Citations

**File:** functions/script_legacy/internal/trace_section.go (L86-92)
```go
func (w *TraceSectionWriter) escape(input string) string {
	if w.posixMode {
		return EscapeForPosix(input)
	}

	return "$'" + EscapeForAnsiC(input) + "'"
}
```

**File:** functions/script_legacy/internal/command_processor.go (L18-26)
```go
// NewCommandProcessor creates a new command processor with the given configuration.
func NewCommandProcessor(config ScriptGeneratorConfig) *CommandProcessor {
	return &CommandProcessor{
		formatter:      NewCommandFormatter(config.PosixEscape),
		sectionWriter:  NewTraceSectionWriter(config.CheckForErrors, config.PosixEscape),
		checkForErrors: config.CheckForErrors,
		traceSections:  config.TraceSections,
	}
}
```

**File:** functions/script_legacy/internal/command_processor.go (L30-47)
```go
func (p *CommandProcessor) ProcessCommand(buf *strings.Builder, index int, command string) {
	command = strings.TrimSpace(command)

	if command == "" {
		buf.WriteString("echo\n")
		return
	}

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

**File:** functions/script_legacy/internal/escape.go (L10-46)
```go
// EscapeForAnsiC escapes a string for use in ANSI-C quoting ($'...').
// Control characters and non-ASCII bytes are hex-escaped to prevent terminal manipulation.
// This prevents jobs from clearing the screen or rewriting logs using ANSI escape sequences.
// Matches GitLab Runner's ShellEscape behavior.
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
