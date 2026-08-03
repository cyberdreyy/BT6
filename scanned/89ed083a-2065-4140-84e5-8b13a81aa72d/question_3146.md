# Q3146: runInContainer helper, build, and service roles blur in exec flows

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services and make `runInContainer` cross helper, build, and service boundaries so one role receives commands, files, or secrets intended for another?

## Target
- File/function: executors/kubernetes/kubernetes.go: runInContainer
- Entrypoint: Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services
- Attacker controls: job script, service definitions, log output, container timing, and reconnect behavior, exec commands, helper outputs, and shared state
- Exploit idea: reuse exec or shared files across role boundaries
- Invariant to test: exec flows must preserve build, helper, and service role separation
- Expected Immunefi impact: secret exposure or stronger-context execution
- Fast validation: exercise mixed-role exec flows and verify role boundaries hold
