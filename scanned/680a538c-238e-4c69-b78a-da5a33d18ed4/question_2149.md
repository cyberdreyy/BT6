# Q2149: createBuildVolume helper and build share the same writable path

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state and make `createBuildVolume` make helper-generated state land on the same writable path later trusted by the build phase?

## Target
- File/function: executors/docker/docker.go: createBuildVolume
- Entrypoint: Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state
- Attacker controls: workspace path state, cache paths, symlinks, repeated jobs, temp files, and job-created residue, shared writable paths between helper and build phases
- Exploit idea: smuggle attacker-influenced files across the helper and build boundary through one mounted path
- Invariant to test: helper and build writable paths must remain isolated where trust differs
- Expected Immunefi impact: stronger-context execution or build-phase hijack
- Fast validation: write helper-side files and verify build phase does not trust them through the same path
