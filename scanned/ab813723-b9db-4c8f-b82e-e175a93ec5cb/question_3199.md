# Q3199: checkScriptExecution rapid service churn routes logs to replacements

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services and make `checkScriptExecution` keep reading logs from a replacement service when the logical job target was the previous instance?

## Target
- File/function: executors/kubernetes/kubernetes.go: checkScriptExecution
- Entrypoint: Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services
- Attacker controls: job script, service definitions, log output, container timing, and reconnect behavior, rapid service replacement and shared selectors
- Exploit idea: reuse target identity across service churn
- Invariant to test: log target identity must remain bound to the intended live instance
- Expected Immunefi impact: wrong-service disclosure or stale-state trust
- Fast validation: replace services during log capture and verify target identity stays exact
