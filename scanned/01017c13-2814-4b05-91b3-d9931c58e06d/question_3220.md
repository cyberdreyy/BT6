# Q3220: captureServiceContainersLogs failure cleanup leaves stale exec or log state alive

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services and make `captureServiceContainersLogs` clean up the wrong exec or log session and leave stale attacker-chosen state alive for reuse?

## Target
- File/function: executors/kubernetes/kubernetes.go: captureServiceContainersLogs
- Entrypoint: Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services
- Attacker controls: job script, service definitions, log output, container timing, and reconnect behavior, cleanup timing and colliding session identities
- Exploit idea: preserve stale mutable session state by diverting cleanup elsewhere
- Invariant to test: failed exec or log sessions must be fully cleaned before later reuse is possible
- Expected Immunefi impact: persistent stale-session hijack or disclosure
- Fast validation: race cleanup against colliding sessions and verify stale state is removed
