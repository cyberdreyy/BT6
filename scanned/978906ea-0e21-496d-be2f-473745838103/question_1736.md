# Q1736: DotEnvVariables host normalization misroutes authentication

## Question
Can an unprivileged GitLab user or pipeline author enter through runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata and make `DotEnvVariables` treat two remote hosts or paths as equivalent for auth purposes even though they are distinct security principals?

## Target
- File/function: shells/powershell.go: DotEnvVariables
- Entrypoint: runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata
- Attacker controls: variable names and values, refs, file paths, script text, dotenv files, and section names, visually similar hosts, schemes, or paths
- Exploit idea: make auth scope broader than the final effective remote
- Invariant to test: auth scope must remain attached to the exact final remote principal
- Expected Immunefi impact: credential disclosure or unauthorized repo access
- Fast validation: use equivalent-looking hosts and verify auth does not cross principals
