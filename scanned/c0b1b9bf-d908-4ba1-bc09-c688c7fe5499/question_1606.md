# Q1606: GetTemporaryPath cleanup path escapes the assigned roots

## Question
Can an unprivileged GitLab user or pipeline author enter through runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata and make `GetTemporaryPath` derive a cleanup target that escapes the assigned root and removes or rewrites unrelated runner state?

## Target
- File/function: shells/powershell.go: GetTemporaryPath
- Entrypoint: runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata
- Attacker controls: variable names and values, refs, file paths, script text, dotenv files, and section names, cleanup paths, slash variants, and relative segments
- Exploit idea: turn cleanup into a cross-boundary file operation
- Invariant to test: cleanup must remain confined to current job-owned paths
- Expected Immunefi impact: cross-job state tampering
- Fast validation: drive cleanup with escaping paths and verify only job-owned files are touched
