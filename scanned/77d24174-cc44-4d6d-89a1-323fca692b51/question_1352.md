# Q1352: buildCommand masked or protected data leaks into generated output

## Question
Can an unprivileged GitLab user or pipeline author enter through runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata and make `buildCommand` print or persist values that should have remained masked or protected?

## Target
- File/function: shells/bash.go: buildCommand
- Entrypoint: runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata
- Attacker controls: variable names and values, refs, file paths, script text, dotenv files, and section names, section names, scripts, and env output
- Exploit idea: route protected data through a generated-output path that is not fully sanitized
- Invariant to test: generated scripts and logs must not reveal masked or protected values
- Expected Immunefi impact: secret exposure across job or project boundaries
- Fast validation: run with masked values and verify they never appear in scripts or logs
