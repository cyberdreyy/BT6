# Q1562: TmpFile special-character variable parsing confusion

## Question
Can an unprivileged GitLab user or pipeline author enter through runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata and make `TmpFile` mis-handle special-character variable names or references so the shell reads a different variable or evaluates unexpected syntax?

## Target
- File/function: shells/powershell.go: TmpFile
- Entrypoint: runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata
- Attacker controls: variable names and values, refs, file paths, script text, dotenv files, and section names, special-character variable names and references
- Exploit idea: trigger parser differences between generated references and intended variable names
- Invariant to test: generated variable references must bind only to the intended variable
- Expected Immunefi impact: secret exposure or command injection
- Fast validation: use special-character variable names and verify the generated script references them safely
