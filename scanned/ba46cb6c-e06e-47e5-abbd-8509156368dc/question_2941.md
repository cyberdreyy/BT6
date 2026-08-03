# Q2941: forwardLogLine exec or attach hits the wrong container

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services and make `forwardLogLine` select one live container and then exec or attach to another after restarts, replacements, or name ambiguity?

## Target
- File/function: executors/kubernetes/kubernetes.go: forwardLogLine
- Entrypoint: Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services
- Attacker controls: job script, service definitions, log output, container timing, and reconnect behavior, container restarts, replacements, and colliding names
- Exploit idea: desynchronize selected exec target from the final live container
- Invariant to test: exec and attach targets must stay bound to one exact live container
- Expected Immunefi impact: job hijack or wrong-container access
- Fast validation: restart containers during exec setup and verify target binding holds
