# Q2158: createBuildVolume mounted files are later sourced or executed

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state and make `createBuildVolume` place attacker-controlled files on a mounted path that later steps source or execute without re-establishing trust?

## Target
- File/function: executors/docker/docker.go: createBuildVolume
- Entrypoint: Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state
- Attacker controls: workspace path state, cache paths, symlinks, repeated jobs, temp files, and job-created residue, filenames that collide with later sourced or executed paths
- Exploit idea: populate later-stage trusted paths through mounted state
- Invariant to test: mounted state must not populate later-executed or later-sourced trusted files
- Expected Immunefi impact: stronger-context execution or secret exposure
- Fast validation: place files on mounted script or env paths and verify later steps do not trust them
