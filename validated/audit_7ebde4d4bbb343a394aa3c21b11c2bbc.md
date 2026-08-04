### Title
Debug-trace (`set -o xtrace`) combined with shell escaping in `Variable` can defeat masking for secrets containing quote/backslash/backtick characters - (File: shells/bash.go)

### Summary
`BashWriter.Variable` (shells/bash.go:229-241) writes masked/protected variable values into the generated script using `b.escape(variable.Value)`, and `BashWriter.Finish` (shells/bash.go:408-445) enables `set -o xtrace` whenever the job sets `CI_DEBUG_TRACE=true` (a normal, unprivileged pipeline-author-controlled variable). The GitLab Runner masker (`common/buildlogger/internal/masker`) only detects a secret if its raw bytes appear contiguously in the trace stream; the escaping performed in `helpers.ShellEscape`/`helpers.PosixShellEscape` inserts extra characters around quote/backslash/backtick/control characters, which can split the secret's contiguous byte run in the emitted `export KEY=...` line that `xtrace` prints, letting part or all of a masked value slip past the masker.

### Finding Description
- `Variable(variable spec.Variable)` (shells/bash.go:229-241) emits `export %s=%s` where the value is passed through `b.escape`, which is either `helpers.ShellEscape` or `helpers.PosixShellEscape` (shells/bash.go:447-453, helpers/shell_escape.go:53-126).
- Both escape functions transform special bytes (`'`, `\`, `` ` ``, `"`, `$`, control chars like `\n`/`\r`/`\t`) into two-character sequences (e.g. `\'`, `\\`, `\n`) and wrap the whole value in `$'...'` (ANSI-C quoting) or `"..."` quoting. This means the on-disk/generated script text for the `export` line no longer contains the raw secret as one contiguous run of bytes whenever the secret contains any of those characters.
- `Finish` (shells/bash.go:408-424) inserts `set -o xtrace` into the script when `info.Build.IsDebugTraceEnabled()` is true, which is controlled by the ordinary `CI_DEBUG_TRACE` job/pipeline variable (confirmed unprivileged and reachable in `common/build_test.go:1074` `TestDebugTrace`, and exercised end-to-end in `executors/shell/shell_integration_test.go:1612` `TestBuildWithDebugTrace`).
- With `xtrace` enabled, bash prints each command line (including the `export KEY=...` line) before executing it, using bash's own re-quoting/escaping of the value, not the runner's original plain value. That printed line, not the raw secret string, is what flows into the job trace/log.
- The runner's masking layer (`common/buildlogger/internal/masker/masker.go`) is a pure byte-substring replacer keyed on the exact `Value` string of every `Masked` variable (`common/spec/variables.go:169-176`, wired up via `Build.getNewLogger`/`MaskPhrases: b.GetAllVariables().Masked()` in `common/build.go:1637-1649`). It has no knowledge of shell quoting/escaping and cannot recognize a secret that has been split or re-encoded by escape insertion.
- Consequence: for a masked variable whose value contains a single quote, backslash, backtick, double quote, or a control character, the byte sequence written into (and echoed back out by) the generated script is no longer identical to the literal secret value that the masker is looking for, so the masker's substring search fails to trigger, and the (partially or fully) unmasked secret is written to the job log/trace.

### Impact Explanation
An unprivileged pipeline author who can set `CI_DEBUG_TRACE=true` (a standard, documented job variable, not admin-gated) and who controls or knows the presence of a masked/protected variable containing shell-special characters can cause that secret's value to appear, unmasked, in the job's trace/log output. This is a masking bypass leading to secret exposure that persists in job logs/traces (which may be visible to other users with log-read access, stored, or exported), matching the "secrets ... must not leak across jobs, projects, logs, traces" invariant.

### Likelihood Explanation
- Precondition: the job must have a `Masked: true` variable whose value contains a byte handled specially by `modeTable`/`posixModeTable` (quote, backslash, backtick, double quote, or control character) and debug trace enabled.
- CI_DEBUG_TRACE is a normal CI variable settable by any pipeline author (not privileged); protected/masked variables commonly do contain such characters (e.g., generated tokens, passwords with punctuation).
- This is fully repeatable and deterministic: any project with such a variable and `CI_DEBUG_TRACE=true` will reproduce it.

### Recommendation
- Either strip/disable `xtrace`/`Set-PSDebug -Trace` whenever any `Masked` variable is present in the job (defense in depth), or
- Make the masker escape-aware: for each masked value, also register masked "escaped" variants (as produced by `ShellEscape`/`PosixShellEscape`/PowerShell quoting) as additional mask phrases, so that the escaped form is also matched and redacted, or
- Avoid embedding secret values directly as literal script text subject to shell re-quoting under `xtrace`; instead source them from files/env indirection that never gets printed verbatim by `xtrace`.

### Proof of Concept
Go integration test (extends `common/buildtest/masking.go` style):
1. Build a job with `spec.Variable{Key: "MASKED_QUOTE", Value: `sec'ret`, Masked: true}` and `spec.Variable{Key: "CI_DEBUG_TRACE", Value: "true"}`.
2. Run the build through the shell executor (as in `executors/shell/shell_integration_test.go` `TestBuildWithDebugTrace`), capturing the trace via `common.Trace`.
3. Assertions:
   - `assert.NotContains(t, output, "sec'ret")` — expected to currently FAIL, demonstrating the secret leaks via the xtrace-printed `export MASKED_QUOTE=$'sec\'ret'` line, whose literal bytes do not match the masker's registered phrase `sec'ret` contiguously.
   - Compare against a control case without special characters (e.g. `Value: "secret"`), which correctly masks, to isolate the escaping-induced bypass.