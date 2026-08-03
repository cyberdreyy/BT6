# Q2962: saveScriptOnEmptyDir build-helper-service role confusion

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs and make `saveScriptOnEmptyDir` blur build, helper, and service roles so a file, env value, or secret crosses into the wrong trust role?

## Target
- File/function: executors/kubernetes/kubernetes.go: saveScriptOnEmptyDir
- Entrypoint: Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs
- Attacker controls: image names, service specs, script artifacts, workspace state, and reconnect timing, build, helper, and service outputs plus shared state
- Exploit idea: smear role boundaries so lower-trust content enters a higher-trust role
- Invariant to test: role separation between build, helper, and service contexts must hold for all user-controlled inputs
- Expected Immunefi impact: secret exposure or stronger-context execution
- Fast validation: exercise mixed-role inputs and verify files and secrets stay in the correct role
