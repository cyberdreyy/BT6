# Q3453: stepsPreparePodConfig target changes after selection but before exec or attach

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs and make `stepsPreparePodConfig` select one workload and then exec, attach, or copy after the target identity has changed?

## Target
- File/function: executors/kubernetes/steps_pod.go: stepsPreparePodConfig
- Entrypoint: Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs
- Attacker controls: image names, service specs, script artifacts, workspace state, and reconnect timing, live restarts, replacements, or renamed targets
- Exploit idea: let final action occur after the selected target has been replaced
- Invariant to test: selection and action must remain bound to one stable workload identity
- Expected Immunefi impact: job hijack or wrong-workload access
- Fast validation: replace targets after selection and verify final actions fail or rebind safely
