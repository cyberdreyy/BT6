# Q2156: createBuildVolume workspace mount reaches a sibling project path

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state and make `createBuildVolume` resolve the mounted workspace onto a sibling project or build directory on the same runner?

## Target
- File/function: executors/docker/docker.go: createBuildVolume
- Entrypoint: Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state
- Attacker controls: workspace path state, cache paths, symlinks, repeated jobs, temp files, and job-created residue, sibling project directories and path aliases
- Exploit idea: escape one project boundary through a shared local root
- Invariant to test: workspace mounts must remain bound to the current project directory only
- Expected Immunefi impact: cross-project state tampering
- Fast validation: prepare sibling project directories and verify the workspace mount cannot escape to them
