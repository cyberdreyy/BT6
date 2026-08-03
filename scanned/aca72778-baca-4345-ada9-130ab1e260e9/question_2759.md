# Q2759: pullDockerImage auth inputs from env, build, or home merge too broadly

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner and make `pullDockerImage` merge auth sources from job env, build state, or home configuration into one overly broad trusted config?

## Target
- File/function: executors/docker/internal/pull/manager.go: pullDockerImage
- Entrypoint: Docker image/service resolution using attacker-controlled image names, registry refs, auth-related variables, and repeated jobs on one runner
- Attacker controls: image refs, registry hosts, auth-related variables, repeated jobs, and locally cached images, multiple auth input sources
- Exploit idea: combine auth sources so lower-trust state expands the final trusted auth set
- Invariant to test: merged auth config must preserve source boundaries and exact scope
- Expected Immunefi impact: credential misuse or wrong-registry access
- Fast validation: supply conflicting auth inputs and verify the final config stays tightly scoped
