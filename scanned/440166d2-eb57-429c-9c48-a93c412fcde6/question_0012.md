# Q0012: ExtractZipArchive validated path retargeted before final write

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact or cache extraction for a job consuming attacker-produced archive content and make `ExtractZipArchive` validate one destination path but write to another after symlink, rename, or directory replacement races inside the restore tree?

## Target
- File/function: helpers/archives/zip_extract.go: ExtractZipArchive
- Entrypoint: artifact or cache extraction for a job consuming attacker-produced archive content
- Attacker controls: archive bytes, entry names, path separators, absolute paths, `..` segments, links, and metadata, restore-tree races and link swaps
- Exploit idea: change the destination after validation but before the final write lands
- Invariant to test: validation and final write must be bound to the same real path
- Expected Immunefi impact: path-root escape or trusted-file overwrite
- Fast validation: race a path swap during restore and verify writes remain confined
