# Q1540: cleanPath repeated setup reuses stale temp or config state

## Question
Can an unprivileged GitLab user or pipeline author enter through runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata and make `cleanPath` reuse stale temp files, git config, or generated shell state from an earlier logical operation?

## Target
- File/function: shells/powershell.go: cleanPath
- Entrypoint: runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata
- Attacker controls: variable names and values, refs, file paths, script text, dotenv files, and section names, retries, repeated stages, and stale temp state
- Exploit idea: have later setup steps trust old state instead of rebuilding from current inputs
- Invariant to test: each logical setup run must get fresh state bound to the current job inputs
- Expected Immunefi impact: cross-job confusion or trusted-runtime reuse
- Fast validation: repeat setup flows and verify stale temp/config state is never reused
