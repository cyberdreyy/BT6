# Q0398: createZipDirectoryEntry hidden files bypass include or exclude assumptions

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections and make `createZipDirectoryEntry` include hidden files or dotfiles that are treated differently from visibly equivalent paths during filtering?

## Target
- File/function: helpers/archives/zip_create.go: createZipDirectoryEntry
- Entrypoint: artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections
- Attacker controls: workspace file tree, symlinks, hardlinks, excluded paths, untracked files, and file metadata, dotfiles and hidden names
- Exploit idea: hide dangerous content behind special-looking names that skip normal filtering paths
- Invariant to test: hidden names must follow the same inclusion and exclusion invariants as visible names
- Expected Immunefi impact: secret exposure or later restore poisoning
- Fast validation: test hidden-name variants and confirm filtering stays consistent
