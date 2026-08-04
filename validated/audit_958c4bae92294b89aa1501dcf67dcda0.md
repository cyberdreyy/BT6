### Title
`psSingleQuote` fails to neutralize Unicode "smart quote" single-quote look-alikes, allowing single-quoted literal breakout - ([File: shells/powershell.go])

### Summary
`psSingleQuote` (shells/powershell.go:232-234) only doubles the ASCII single quote (`U+0027`) to escape embedded quotes, but PowerShell's tokenizer treats the Unicode "smart quote" single-quote family (`U+2018 ‘`, `U+2019 ’`, `U+201A ‚`, `U+201B ‛`) as interchangeable string terminators for a string opened with `'`. Since `Command`/`IfCmd`/`SetupGitCredHelper`/`CommandWithStdin` all feed job/argument-controlled strings through `psSingleQuote` into `buildCommand`'s `& cmd arg1 arg2 ...` invocation, an attacker-controlled argument containing one of these look-alike characters followed by PowerShell code can terminate the intended single-quoted literal early and inject a new statement that executes in the job's PowerShell process.

### Finding Description
`psSingleQuote` is:
```go
func psSingleQuote(text string) string {
	return "'" + strings.ReplaceAll(text, "'", "''") + "'"
}
```
It escapes only the literal ASCII `'`. The sibling function `psDoubleQuote` (lines 237-243) explicitly patches an analogous, documented PowerShell tokenizer quirk for double-quoted strings by backtick-escaping the Unicode curly double-quote variants `“`(U+201C), `”`(U+201D), `„`(U+201E), citing PowerShell's `CharTraits.cs`. This is a direct acknowledgment by the codebase that the underlying PowerShell parser treats certain Unicode punctuation as equivalent to the ASCII quote character it visually resembles when scanning for a string terminator. No corresponding fix exists for the single-quote family (`‘ U+2018`, `’ U+2019`, `‚ U+201A`, `‛ U+201B`) in `psSingleQuote`.

`psSingleQuote` output is used directly as command/argument tokens inside `buildCommand` (lines 384-394), which builds the `& "cmd" 'arg1' 'arg2' ...` invocation consumed by `Command` (line 281-284) and `IfCmd`/`IfCmdWithOutput` (lines 495-501) — call sites that receive job-script-derived command/argument strings (e.g., built from CI job variables and configuration passed by the pipeline author, which is unprivileged, attacker-controlled input). If such an argument contains a smart-quote look-alike character followed by injected PowerShell syntax (e.g., a subexpression `$(...)` or a type accelerator invocation), and PowerShell's real tokenizer accepts that Unicode character as a valid closing delimiter for the ASCII-opened `'...'` literal, the string terminates prematurely and the remaining attacker-supplied text is parsed as live PowerShell code rather than as an inert string, breaking the confinement of the `& { command args }` wrapper.

### Impact Explanation
If the tokenizer behavior is confirmed (as the codebase's own `psDoubleQuote` fix for the double-quote analog strongly implies), this allows arbitrary PowerShell code execution within the job process, escaping the intended single-command scoping of `buildCommand`. Because argument values commonly derive from job-controlled data (script content, git credential helper values, variables passed to `Command`/`CommandWithStdin`), an unprivileged pipeline author can smuggle a payload without needing an ASCII `'` (which is otherwise safely doubled), defeating the only quoting defense in this path.

### Likelihood Explanation
Exploitability depends entirely on whether the specific PowerShell/pwsh version(s) supported by Runner treat the curly single-quote code points as valid terminators for an ASCII-opened `'...'` string during tokenization — the same class of behavior the code already patches for double quotes. I could not directly execute PowerShell's tokenizer to confirm this for single quotes in this environment; this determination requires validating against `System.Management.Automation.Language.CharTraits`/`Tokenizer.cs` for the PowerShell version(s) Runner targets. Given the precedent fix in `psDoubleQuote`, it is plausible but unconfirmed from static review alone.

### Recommendation
Mirror the `psDoubleQuote` mitigation in `psSingleQuote`: escape/neutralize `U+2018`, `U+2019`, `U+201A`, and `U+201B` (e.g., via backtick-escaping or explicit replacement) in addition to doubling the ASCII `'`, so no Unicode single-quote look-alike can survive unescaped inside a single-quoted PowerShell literal.

### Proof of Concept
Go fuzz/unit test (`shells/powershell_test.go`):
```go
func TestPsSingleQuoteSmartQuoteBreakout(t *testing.T) {
    payload := "innocuous\u2019;iex('calc.exe');'"
    quoted := psSingleQuote(payload)
    // Feed `quoted` into an actual PowerShell/pwsh binary via
    // `pwsh -NoProfile -Command "& { $x = ''+quoted+''; $x }"`
    // or use a PowerShell AST parser to confirm token boundaries.
    // Assertion: the parsed AST/tokenizer must treat the entire `quoted`
    // value as a single StringConstantExpressionAst, not multiple statements.
}
```
Confirm with an actual `pwsh -Command` invocation (or the PowerShell AST parser) that a string built by `psSingleQuote` containing `U+2019` is parsed as one literal token; if the parser splits at `U+2019` and executes the trailing `iex(...)`, the vulnerability is proven.