# Q1970: generateSaveScript checkout or include path escapes the workspace

## Question
Can an unprivileged GitLab user or pipeline author enter through runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata and make `generateSaveScript` resolve checkout, include, or config paths outside the assigned workspace and temp roots?

## Target
- File/function: shells/powershell.go: generateSaveScript
- Entrypoint: runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata
- Attacker controls: variable names and values, refs, file paths, script text, dotenv files, and section names, checkout and include paths
- Exploit idea: drive path resolution onto external or sibling runner paths
- Invariant to test: checkout and include paths must remain inside the assigned workspace and temp roots
- Expected Immunefi impact: cross-job tampering or helper-state overwrite
- Fast validation: supply escaping checkout/include paths and verify confinement
