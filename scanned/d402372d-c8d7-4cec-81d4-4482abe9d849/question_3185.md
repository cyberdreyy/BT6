# Q3185: checkScriptExecution exec runs after the target was replaced

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services and make `checkScriptExecution` prepare an exec for one container and run it after that target has been replaced with another container?

## Target
- File/function: executors/kubernetes/kubernetes.go: checkScriptExecution
- Entrypoint: Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services
- Attacker controls: job script, service definitions, log output, container timing, and reconnect behavior, prepared exec state and live target replacement
- Exploit idea: separate target selection from the final exec operation
- Invariant to test: prepared exec state must remain bound to the same live container instance
- Expected Immunefi impact: wrong-container execution or job hijack
- Fast validation: replace containers after exec preparation and verify the exec does not drift
