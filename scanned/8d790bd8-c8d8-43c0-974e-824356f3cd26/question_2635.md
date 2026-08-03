# Q2635: waitForServiceContainer helper bootstrap writes files later trusted by the build phase

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state and make `waitForServiceContainer` write helper or bootstrap files that the build phase later trusts without re-establishing ownership or origin?

## Target
- File/function: executors/docker/services.go: waitForServiceContainer
- Entrypoint: Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state
- Attacker controls: image names, service definitions, job output, artifact/cache residue, container timing, and repeated jobs on one runner, shared files between helper and build phases
- Exploit idea: smuggle attacker-influenced files across a helper/build trust boundary
- Invariant to test: build phase must not trust helper-written attacker content without explicit rebinding
- Expected Immunefi impact: stronger-context execution or build-phase hijack
- Fast validation: place attacker-influenced helper files and verify the build phase does not trust them
