# Q3437: setupStepsPod secrets become visible in the wrong role or phase

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs and make `setupStepsPod` expose projected secrets or credentials to a role or phase that should not receive them?

## Target
- File/function: executors/kubernetes/steps_pod.go: setupStepsPod
- Entrypoint: Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs
- Attacker controls: image names, service specs, script artifacts, workspace state, and reconnect timing, build, helper, service, or phase transitions
- Exploit idea: allow secret-bearing state to cross one role boundary too far
- Invariant to test: secret projection must remain limited to the intended role and phase
- Expected Immunefi impact: secret exposure across role boundaries
- Fast validation: trace secret presence across phases and verify it is never visible in attacker-controlled roles
