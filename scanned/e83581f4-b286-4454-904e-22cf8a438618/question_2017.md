# Q2017: bashDetectScript partial resolver failure keeps attacker-selected output

## Question
Can an unprivileged GitLab user or pipeline author enter through runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata and make `bashDetectScript` fail partially while preserving attacker-selected secret or env output that replaces trusted defaults?

## Target
- File/function: shells/bash.go: bashDetectScript
- Entrypoint: runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata
- Attacker controls: variable names and values, refs, file paths, script text, dotenv files, and section names, partial resolver errors and fallback values
- Exploit idea: retain partial output on error and let later phases trust it
- Invariant to test: failed resolution must not keep partial untrusted output as valid config
- Expected Immunefi impact: trusted-runtime override or secret confusion
- Fast validation: force partial resolution errors and verify no partial output survives
