### Title
EscapeForPosix fails to hex-escape control/ANSI bytes, allowing terminal/log injection in POSIX-mode command echo - ([File: functions/script_legacy/internal/escape.go])

### Summary
`EscapeForAnsiC` hex-escapes control characters (0x00–0x1F, 0x7F) and non-ASCII bytes specifically to stop a job's command text from injecting raw ANSI escape sequences into the trace log, but `EscapeForPosix` — used for the exact same purpose when `posixMode` is enabled — only escapes `` ` ``, `"`, `\`, `$` and a small metacharacter set, passing all control bytes through unmodified. Since both functions back the same `echo`/`printf` command-echoing code paths (`CommandFormatter.FormatLogLine`, `TraceSectionWriter.escape`), a job run in POSIX mode can inject raw ESC (0x1B) sequences into the trace stream that the ANSI-C path is explicitly designed to block.

### Finding Description
`EscapeForAnsiC` (functions/script_legacy/internal/escape.go:14-50) hex-escapes any byte `< 0x20`, `== 0x7F`, or `> 0x7F` specifically "to prevent jobs from clearing the screen or rewriting logs using ANSI escape sequences," per its own doc comment. `EscapeForPosix` (functions/script_legacy/internal/escape.go:55-82) has no equivalent handling: its `default` case at line 72-73 writes any rune — including ESC (0x1B), CSI sequences, or other control bytes — straight into the double-quoted output with no escaping at all.

Both functions are wired into the same log-echoing logic, selected only by a `posixMode` flag:
- `CommandFormatter.FormatLogLine` (functions/script_legacy/internal/command_formatter.go:31-39): posix mode → `echo <EscapeForPosix(command)>`; non-posix → `echo $'<EscapeForAnsiC(command)>'`.
- `TraceSectionWriter.escape` (functions/script_legacy/internal/trace_section.go:86-92): identical dispatch for the `printf` section-header/command lines.

In both cases, `command` embeds attacker/job-author-controlled content (the job's own script text, which is echoed back into the trace for display, e.g. `ansiGreen + commandPrefix + f.getDisplayCommand(command) + ansiReset`). If a job's script line itself contains raw ESC bytes (e.g. a step whose source line was crafted to include `\x1b[2J` or cursor-repositioning sequences — trivially achievable since CI YAML/script content is fully job-author-controlled), that byte survives untouched through `EscapeForPosix` and reaches the shell's `echo "..."` unescaped, and from there into the job trace/log stream verbatim. In double-quoted POSIX shell strings, raw ESC bytes are not shell metacharacters and require no escaping to pass through — so the shell will emit them exactly as: this is precisely the terminal-manipulation/log-spoofing scenario `EscapeForAnsiC` was written to prevent, but is not prevented when `posixMode` is true.

### Impact Explanation
A job running with `posixMode` enabled can inject raw ANSI/control sequences into its own trace output via the command-echo mechanism, e.g. to clear/rewrite previously printed log lines (log spoofing) or manipulate the CI log viewer terminal rendering, undermining the protection that `EscapeForAnsiC` explicitly implements for the non-posix path. This is a log-integrity/spoofing issue: it lets a job forge or hide entries in its own build log, which can be used to make malicious commands invisible to a reviewer of the job log or to fake status output. It does not provide sandbox escape or cross-job/cross-project access.

### Likelihood Explanation
Fully attacker-controlled and trivially reproducible: any job author can put raw ESC bytes into a script line (or into a variable substituted into the script before this formatting step) and set/trigger the `posixMode` execution path. No special privileges are required beyond authoring a pipeline, which matches the "unprivileged pipeline author" threat model.

### Recommendation
Make `EscapeForPosix` hex-escape (or otherwise neutralize) control characters and DEL/non-printable bytes the same way `EscapeForAnsiC` does, e.g. by emitting `\xNN` sequences via ANSI-C quoting (`$'...'`) instead of plain double quotes when control bytes are present, or by stripping/hex-escaping bytes `< 0x20 || == 0x7F` before wrapping in `"..."`. Alternatively, unify both code paths to always route control-character sanitization through one shared function so the two quoting styles can't diverge on the security-relevant byte set again.

### Proof of Concept
```go
func TestEscapeForPosix_DoesNotEscapeControlChars(t *testing.T) {
    input := "\x1b[2Jinjected"
    got := EscapeForPosix(input)
    // BUG: raw ESC byte passes through unescaped
    assert.Contains(t, got, "\x1b[2J", "ESC sequence should be neutralized like EscapeForAnsiC does, but is not")
}
```
Integration-level PoC: construct a job script line containing `\x1b[2J` (or another CSI clear/cursor sequence), run it through `CommandFormatter.FormatLogLine` with `posixMode=true`, execute the resulting `echo "..."` under `sh`, and assert the captured output byte stream still contains the raw ESC byte (0x1B) — demonstrating the same terminal-manipulation protection asserted by `TestEscapeForAnsiC_SecurityFeatures` is absent for the POSIX path.

Note: the divergence in how `$`, `` ` `` are handled between the two functions is *not* a bug by itself — `EscapeForAnsiC`'s output is always wrapped in ANSI-C quoting (`$'...'`, confirmed at command_formatter.go:38 and trace_section.go:91), where `$` and backtick are literal and require no escaping, while `EscapeForPosix`'s output is wrapped in POSIX double quotes where those characters do trigger expansion and correctly must be escaped. The real, valid divergence is specifically the control-character/ANSI-escape handling described above.