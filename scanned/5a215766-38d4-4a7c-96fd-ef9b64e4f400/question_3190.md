# Q3190: checkScriptExecution permission-init or bootstrap files cross into the wrong role

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services and make `checkScriptExecution` use bootstrap or permission-fixing behavior to make attacker files appear trusted in a later role?

## Target
- File/function: executors/kubernetes/kubernetes.go: checkScriptExecution
- Entrypoint: Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services
- Attacker controls: job script, service definitions, log output, container timing, and reconnect behavior, shared files across init, helper, and build roles
- Exploit idea: upgrade attacker-controlled files through earlier privileged handling inside the pod
- Invariant to test: bootstrap and permission steps must not elevate attacker files across roles
- Expected Immunefi impact: stronger-context execution or secret exposure
- Fast validation: place attacker files on shared paths and verify bootstrap does not upgrade trust
