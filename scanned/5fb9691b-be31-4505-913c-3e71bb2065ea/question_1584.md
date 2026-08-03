# Q1584: GetTemporaryPath env or dotenv path escapes temp roots

## Question
Can an unprivileged GitLab user or pipeline author enter through runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata and make `GetTemporaryPath` read from or write to an env file outside the assigned workspace and temp roots?

## Target
- File/function: shells/bash.go: GetTemporaryPath
- Entrypoint: runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata
- Attacker controls: variable names and values, refs, file paths, script text, dotenv files, and section names, env-file paths and path aliases
- Exploit idea: select env-file locations that resolve outside assigned temp roots
- Invariant to test: env and dotenv files must remain inside the assigned workspace and temp roots
- Expected Immunefi impact: trusted-runtime override or secret-bearing file overwrite
- Fast validation: supply escaping env-file paths and verify the operation is rejected or confined
