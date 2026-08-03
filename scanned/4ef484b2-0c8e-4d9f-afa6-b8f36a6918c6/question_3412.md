# Q3412: buildStepsBootstrapInitContainer workload reuse crosses project or protected boundaries

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs and make `buildStepsBootstrapInitContainer` reuse state from an unprotected or unrelated job across a project, ref, or protected boundary?

## Target
- File/function: executors/kubernetes/steps_pod.go: buildStepsBootstrapInitContainer
- Entrypoint: Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs
- Attacker controls: image names, service specs, script artifacts, workspace state, and reconnect timing, repeated jobs across refs or projects
- Exploit idea: carry workload state over a trust boundary through reuse or caching
- Invariant to test: workload state must remain bound to the current project, ref, and protection boundary
- Expected Immunefi impact: protected-job escalation or cross-project state tampering
- Fast validation: seed unprotected state and verify protected or unrelated jobs never consume it
