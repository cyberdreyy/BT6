# Q1359: buildCommand path normalization mismatch bypasses checks

## Question
Can an unprivileged GitLab user or pipeline author enter through runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata and make `buildCommand` validate one path spelling but operate on another equivalent path after normalization differences?

## Target
- File/function: shells/bash.go: buildCommand
- Entrypoint: runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata
- Attacker controls: variable names and values, refs, file paths, script text, dotenv files, and section names, slash, backslash, case, or drive aliases
- Exploit idea: pass validation on one representation and execute on another equivalent path
- Invariant to test: validation and final path use must agree on one canonical in-root path
- Expected Immunefi impact: cross-job tampering or stronger-context file overwrite
- Fast validation: exercise path aliases and verify consistent canonicalization
