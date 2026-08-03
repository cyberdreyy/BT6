# Q1741: SourceEnv literal data turns into shell execution

## Question
Can an unprivileged GitLab user or pipeline author enter through runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata and make `SourceEnv` turn attacker-controlled text that should stay literal into shell syntax that executes in a stronger runner context?

## Target
- File/function: shells/bash.go: SourceEnv
- Entrypoint: runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata
- Attacker controls: variable names and values, refs, file paths, script text, dotenv files, and section names, characters meaningful to the target shell
- Exploit idea: smuggle shell syntax through quoting or interpolation boundaries
- Invariant to test: runner-generated shell commands, env files, temp paths, and cleanup paths must treat attacker text as data, not executable syntax
- Expected Immunefi impact: stronger-context command execution
- Fast validation: feed shell metacharacters through the entrypoint and verify literal preservation
