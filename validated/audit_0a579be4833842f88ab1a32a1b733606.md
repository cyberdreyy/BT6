### Title
`EscapeForPosix` fails to neutralize ANSI/control-character sequences, allowing pipeline-authored trace/log spoofing - (functions/script_legacy/internal/escape.go)

### Summary
`EscapeForPosix` only escapes the characters `` ` ``, `"`, `\`, `$` and only *quotes* the result if it also contains a small set of shell metacharacters (space, `!`, `#`, `%`, `&`, `(`, `)`, `*`, `<`, `=`, `>`, `?`, `[`, `|`). Unlike its sibling `EscapeForAnsiC`, it does not hex-escape control bytes (0x00-0x1F, 0x7F) or the ESC byte (0x1B), so raw ANSI escape/control sequences embedded in a user's own job command pass through untouched into the generated `echo`/`printf` trace lines.

### Finding Description
`CommandFormatter.FormatLogLine` (functions/script_legacy/internal/command_formatter.go:31-39) builds the "$ <command>" preview line and, when `posixMode` is enabled, sanitizes it with `EscapeForPosix` before wrapping it in `echo %s`. `TraceSectionWriter.writeSectionStart` (functions/script_legacy/internal/trace_section.go:52-62) does the same for the collapsible trace-section header via `escape()`, which also calls `EscapeForPosix` in POSIX mode.

`EscapeForPosix` (functions/script_legacy/internal/escape.go:55-82) only special-cases `` ` "\$ `` (escaped) and ` !#%&()*<=>?[|` (marks quoting needed); every other byte, including control characters and the ESC byte (0x1B) that begins ANSI escape sequences, `\r`, `\b`, etc., falls into the `default` branch and is written through **verbatim, unescaped**. Compare this to `EscapeForAnsiC` (escape.go:14-50), whose comment explicitly states its purpose is "to prevent jobs from clearing the screen or rewriting logs using ANSI escape sequences" by hex-escaping bytes `< 0x20`, `0x7F`, and `> 0x7F`. `EscapeForPosix` has no equivalent protection.

Because `command` originates directly from the pipeline author's own `.gitlab-ci.yml` script content, an attacker fully controls its bytes, including raw ESC/CR bytes. When `PosixEscape` (POSIX-mode escaping) is the active code path, the sanitized "$ <command>" preview and/or trace-section header line is echoed into the job trace with the attacker's raw terminal control sequences intact.

### Impact Explanation
An unprivileged pipeline author can embed ANSI escape/control sequences (e.g., `\x1b[2K\r`) in a script command. When rendered by a terminal-aware log viewer, these sequences can erase or overwrite the "$ <command>" preview line (and any following legitimately-echoed content), letting the attacker present a falsified/benign-looking command in the visible job trace while a different, hidden command actually executed. This is a trace/log integrity violation (spoofing what a reviewer sees ran in the job), which is exactly the class of attack `EscapeForAnsiC`'s comment says it defends against — that defense is missing from the POSIX path.

### Likelihood Explanation
Fully feasible and repeatable: it only requires a CI job configured to run with POSIX-mode command escaping enabled and a script line containing bytes like `\x1b` or `\r`. No special privileges, admin cooperation, or race conditions are needed — any pipeline author can trigger it deterministically on every run.

### Recommendation
Align `EscapeForPosix` with `EscapeForAnsiC`: hex-escape (or otherwise neutralize, e.g. via `$'...'`-style `\xHH` inside the POSIX-safe representation, or by refusing to leave the string unquoted) all bytes `< 0x20`, `0x7F`, and non-ASCII bytes `> 0x7F`, in addition to the existing `` `"\$ `` handling, so raw control/ANSI sequences can never reach the trace unescaped.

### Proof of Concept
Go unit test in `functions/script_legacy/internal/escape_test.go`:
```go
func TestEscapeForPosix_DoesNotNeutralizeAnsiEscape(t *testing.T) {
    input := "\x1b[2K\rmalicious hidden command"
    out := EscapeForPosix(input)
    // Expect the ESC byte to be neutralized (hex-escaped), matching EscapeForAnsiC's behavior.
    assert.NotContains(t, out, "\x1b", "raw ESC byte must not survive POSIX escaping")
}
```
Currently this assertion fails because `out` contains the raw `\x1b` byte verbatim, proving the escape sequence reaches the generated `echo`/`printf` trace line unmodified.