# Q1871: generateScript resolved secrets overwrite trusted runtime env

## Question
Can an unprivileged GitLab user or pipeline author enter through runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata and make `generateScript` let secret-derived or file-derived values overwrite trusted runtime variables such as auth, cache, or helper settings?

## Target
- File/function: shells/bash.go: generateScript
- Entrypoint: runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata
- Attacker controls: variable names and values, refs, file paths, script text, dotenv files, and section names, variable names that collide with trusted runtime keys
- Exploit idea: replace trusted runtime config with attacker-selected resolved values
- Invariant to test: resolved variables must not override trusted runner-runtime settings across trust boundaries
- Expected Immunefi impact: secret exposure, wrong-target auth, or later job hijack
- Fast validation: resolve colliding variable names and verify protected runtime keys remain unchanged
