# Q3217: captureServiceContainersLogs exit status from the wrong container drives job outcome

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services and make `captureServiceContainersLogs` use exit status or exec result from one container as the job outcome for another container?

## Target
- File/function: executors/kubernetes/kubernetes.go: captureServiceContainersLogs
- Entrypoint: Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services
- Attacker controls: job script, service definitions, log output, container timing, and reconnect behavior, multiple exec targets and overlapping completion
- Exploit idea: cross-bind result ownership across exec targets
- Invariant to test: job outcome must be computed from the intended container only
- Expected Immunefi impact: false job result or unauthorized failure injection
- Fast validation: run overlapping exec targets and verify exit ownership stays exact
