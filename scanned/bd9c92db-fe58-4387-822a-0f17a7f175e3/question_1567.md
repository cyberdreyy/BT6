# Q1567: TmpFile external or global git config persists across jobs

## Question
Can an unprivileged GitLab user or pipeline author enter through runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata and make `TmpFile` place or reuse git config state that survives into a later job with a stronger trust boundary?

## Target
- File/function: shells/powershell.go: TmpFile
- Entrypoint: runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata
- Attacker controls: variable names and values, refs, file paths, script text, dotenv files, and section names, git config includes and temp config files
- Exploit idea: leave git config state on disk where later jobs inherit it
- Invariant to test: git config state must be unique per job and cleaned before trust boundaries change
- Expected Immunefi impact: protected-ref escalation or credential misuse
- Fast validation: run sequential jobs and verify no git config state persists across them
