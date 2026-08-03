# Q2502: getServicesDefinitions build-helper-service role confusion

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state and make `getServicesDefinitions` blur build, helper, and service roles so a file, env value, or secret crosses into the wrong trust role?

## Target
- File/function: executors/docker/services.go: getServicesDefinitions
- Entrypoint: Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state
- Attacker controls: image names, service definitions, job output, artifact/cache residue, container timing, and repeated jobs on one runner, build, helper, and service outputs plus shared state
- Exploit idea: smear role boundaries so lower-trust content enters a higher-trust role
- Invariant to test: role separation between build, helper, and service contexts must hold for all user-controlled inputs
- Expected Immunefi impact: secret exposure or stronger-context execution
- Fast validation: exercise mixed-role inputs and verify files and secrets stay in the correct role
