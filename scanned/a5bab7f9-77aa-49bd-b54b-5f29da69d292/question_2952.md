# Q2952: forwardLogLine container-role selection ignores the intended role

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services and make `forwardLogLine` select the wrong container role for exec or log capture even though the job intended another role?

## Target
- File/function: executors/kubernetes/kubernetes.go: forwardLogLine
- Entrypoint: Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services
- Attacker controls: job script, service definitions, log output, container timing, and reconnect behavior, container-role names and repeated operations
- Exploit idea: let role selection drift to another live container
- Invariant to test: container-role selection must remain exact for every exec or log action
- Expected Immunefi impact: wrong-container access or role confusion
- Fast validation: exercise multiple container roles and verify exact target selection
