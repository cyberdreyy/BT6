# Q1634: Variable lower-trust repo state survives into protected jobs

## Question
Can an unprivileged GitLab user or pipeline author enter through runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata and make `Variable` preserve lower-trust checkout or config residue until a protected or unrelated ref consumes it?

## Target
- File/function: shells/bash.go: Variable
- Entrypoint: runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata
- Attacker controls: variable names and values, refs, file paths, script text, dotenv files, and section names, repeated jobs across protected and unprotected refs
- Exploit idea: leave repo state behind and rely on reuse across trust boundaries
- Invariant to test: source checkout and config state must stay bound to the current ref and protection level
- Expected Immunefi impact: protected-ref escalation via checkout-state reuse
- Fast validation: seed lower-trust repo state and verify protected refs rebuild or isolate state
