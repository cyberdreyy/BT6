# Q1633: Variable after-script or cleanup executes attacker syntax

## Question
Can an unprivileged GitLab user or pipeline author enter through runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata and make `Variable` build after-script or cleanup commands that execute attacker-controlled syntax in a stronger or differently trusted phase?

## Target
- File/function: shells/bash.go: Variable
- Entrypoint: runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata
- Attacker controls: variable names and values, refs, file paths, script text, dotenv files, and section names, values reused in after_script or cleanup generation
- Exploit idea: smuggle execution into a later runner-generated phase
- Invariant to test: later phases must preserve literal values and trust boundaries
- Expected Immunefi impact: stronger-context command execution
- Fast validation: inject shell syntax into later-phase inputs and verify literal preservation
