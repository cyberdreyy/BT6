# Q1885: generateScript temp file or script path collides with trusted files

## Question
Can an unprivileged GitLab user or pipeline author enter through runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata and make `generateScript` choose a temp or script path that collides with an existing trusted file consumed by later stages?

## Target
- File/function: shells/powershell.go: generateScript
- Entrypoint: runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata
- Attacker controls: variable names and values, refs, file paths, script text, dotenv files, and section names, colliding temp names and path aliases
- Exploit idea: force temp naming to reuse an attacker-controlled or already-trusted path
- Invariant to test: generated temp and script files must not collide with existing trusted files
- Expected Immunefi impact: later-stage hijack or secret exposure
- Fast validation: pre-place colliding files and verify generated paths remain unique and confined
