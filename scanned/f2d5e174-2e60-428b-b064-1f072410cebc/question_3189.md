# Q3189: checkScriptExecution shared script or log paths are trusted later

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services and make `checkScriptExecution` place attacker-controlled files onto script or log paths that later exec or attach phases trust?

## Target
- File/function: executors/kubernetes/kubernetes.go: checkScriptExecution
- Entrypoint: Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services
- Attacker controls: job script, service definitions, log output, container timing, and reconnect behavior, shared script paths and colliding log files
- Exploit idea: use shared volume paths to smuggle attacker content into trusted exec inputs
- Invariant to test: script and log paths must remain isolated from attacker-controlled shared content
- Expected Immunefi impact: stronger-context execution or output tampering
- Fast validation: populate shared script or log paths and verify later phases do not trust them
