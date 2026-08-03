# Q3491: stepsServiceContainers bootstrap script path collides with attacker files

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs and make `stepsServiceContainers` place bootstrap or helper scripts onto paths the attacker can pre-populate or collide with?

## Target
- File/function: executors/kubernetes/steps_pod.go: stepsServiceContainers
- Entrypoint: Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs
- Attacker controls: image names, service specs, script artifacts, workspace state, and reconnect timing, pre-populated files and colliding script paths
- Exploit idea: cause runner-generated bootstrap content to reuse attacker-chosen files or names
- Invariant to test: bootstrap paths must be isolated and unique per job
- Expected Immunefi impact: stronger-context execution or helper-state hijack
- Fast validation: pre-create colliding files and verify bootstrap paths remain isolated
