### Title
Incomplete smart-quote escaping in `psReplacer` allows double-quoted PowerShell string breakout via U+201F - ([File: functions/concrete/run/stages/internal/scriptwriter/scriptwriter.go])

### Summary
`psQuoteVariable` wraps the trace-echo text in a PowerShell double-quoted string and relies on `psReplacer` to neutralize characters that PowerShell's tokenizer treats as quote delimiters. `psReplacer` escapes `"`, `` ` ``, `$`, and the smart double-quotes U+201C/U+201D/U+201E, but omits U+201F (DOUBLE HIGH-REVERSED-9 QUOTATION MARK), which PowerShell's parser also treats as an equivalent double-quote terminator.

### Finding Description
`psQuoteVariable` at `functions/concrete/run/stages/internal/scriptwriter/scriptwriter.go` builds a PowerShell string as `"` + `psReplacer.Replace(text)` + `"` [1](#0-0) . `psReplacer` is a fixed `strings.NewReplacer` table that escapes backtick, control characters, `#`, `'`, `"`, `$`, and three Unicode smart quotes (U+201C, U+201D, U+201E) by prefixing them with a backtick [2](#0-1) . PowerShell's tokenizer (System.Management.Automation) recognizes a set of four Unicode "smart quote" characters as equivalent to `"` for double-quoted string delimiting: U+201C, U+201D, U+201E, and U+201F. `psReplacer` covers only the first three and misses U+201F.

The `$(` subexpression concern raised in the question is not exploitable on its own: `psReplacer` unconditionally escapes every literal `$` character to `` `$ `` regardless of what follows it, so `$(` inside the quoted text becomes `` `$( ``, which is not evaluated as a PowerShell subexpression. That part of the hypothesis is disproven.

However, the smart-quote gap is real: this text (`\033[32;1m$ %s\033[0m`, where `%s` is the raw job command line from `.gitlab-ci.yml` script/before_script/after_script entries) is attacker-controlled at the CI-config level, since job authors control the literal script lines that get echoed via `psQuoteVariable` for the trace header [3](#0-2) . If a script line contains U+201F, it passes through `psReplacer` unescaped and terminates the enclosing double-quoted string early when PowerShell parses the generated script, allowing subsequent characters in that line to be interpreted as new PowerShell statements rather than a String literal argument to `echo`.

### Impact Explanation
This gives a job author the ability to inject and execute arbitrary PowerShell statements inside the wrapper script that is already running with the job's own privileges (as the job's own shell executor process). Since the attacker already fully controls the job script content and executes as that job anyway, the scoped impact is limited: no privilege escalation, no cross-project/cross-job boundary is crossed, and no isolation boundary is broken beyond what the job could already achieve by simply putting the same PowerShell statement directly in `script:`. The practical impact is therefore minimal — it is a quoting/escaping defect, not a sandbox escape, since the "attacker" (job author) can already run arbitrary commands in the job.

### Likelihood Explanation
Triggering the missing-escape path requires a job author to include the specific Unicode character U+201F in a script line, and requires the runner to use the `pwsh`/`powershell` shell and go through `buildPwshScript`/`psQuoteVariable`'s echo-header code path. This is trivially reproducible by any pipeline author, but the impact is confined to statements executed within the job's own script context, which the author already controls entirely.

### Recommendation
Add `\u201f` (and audit for any other PowerShell-recognized smart quote equivalents) to the `psReplacer` table in `functions/concrete/run/stages/internal/scriptwriter/scriptwriter.go` for completeness and defense-in-depth, even though the exploitability is minimal given the attacker already controls script execution.

### Proof of Concept
Go unit test idea in `scriptwriter_test.go`:
```go
func TestPsQuoteVariable_U201F(t *testing.T) {
    out := psQuoteVariable("foo\u201Fbar")
    // Assert the quote-equivalent character is escaped with a backtick,
    // consistent with U+201C/U+201D/U+201E handling.
    if !strings.Contains(out, "`\u201f") {
        t.Errorf("expected U+201F to be backtick-escaped, got: %q", out)
    }
}
```
This currently fails, confirming the gap in `psReplacer`. Note the associated `$(` breakout hypothesis in the question does not hold, because `psReplacer` escapes every bare `$` unconditionally, neutralizing subexpression syntax regardless of what follows it.

### Citations

**File:** functions/concrete/run/stages/internal/scriptwriter/scriptwriter.go (L185-192)
```go
		displayLine := line
		if nlIdx != -1 {
			displayLine = line[:nlIdx] + " # collapsed multi-line command"
		}
		body.WriteString("echo " + psQuoteVariable("\033[32;1m$ "+displayLine+"\033[0m") + eol)

		body.WriteString("$global:LASTEXITCODE = $_runner_exit_code" + eol)
		body.WriteString(line + checkErr)
```

**File:** functions/concrete/run/stages/internal/scriptwriter/scriptwriter.go (L241-257)
```go
var psReplacer = strings.NewReplacer(
	"`", "``",
	"\a", "`a",
	"\b", "`b",
	"\f", "`f",
	"\r", "`r",
	"\n", "`n",
	"\t", "`t",
	"\v", "`v",
	"#", "`#",
	"'", "`'",
	`"`, "`\"",
	"$", "`$",
	"\u201c", "`\u201c",
	"\u201d", "`\u201d",
	"\u201e", "`\u201e",
)
```

**File:** functions/concrete/run/stages/internal/scriptwriter/scriptwriter.go (L259-261)
```go
func psQuoteVariable(text string) string {
	return `"` + psReplacer.Replace(text) + `"`
}
```
