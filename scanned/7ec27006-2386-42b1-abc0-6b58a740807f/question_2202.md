# Q2202: createVolumes symlink pivot inside a mounted workspace

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state and make `createVolumes` follow or preserve an in-workspace link so writes land on a runner-owned path outside the mounted root?

## Target
- File/function: executors/docker/docker.go: createVolumes
- Entrypoint: Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state
- Attacker controls: workspace path state, cache paths, symlinks, repeated jobs, temp files, and job-created residue, workspace symlinks and replaced directories
- Exploit idea: use a link inside the mounted tree as a pivot to external state
- Invariant to test: mounted workspaces must not follow attacker-controlled links outside the assigned roots
- Expected Immunefi impact: cross-job tampering through mount escape
- Fast validation: create in-workspace links and verify mounts never write through them to external paths
