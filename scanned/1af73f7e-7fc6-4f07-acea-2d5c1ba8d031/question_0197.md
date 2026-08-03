# Q0197: getExtractorOptionsFromEnvironment hidden entries bypass restore filters

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact or cache extraction for a job consuming attacker-produced archive content and make `getExtractorOptionsFromEnvironment` accept hidden or special-looking entry names that bypass restore filters but later land on trusted paths?

## Target
- File/function: commands/helpers/archive/fastzip/zip_fastzip_extractor.go: getExtractorOptionsFromEnvironment
- Entrypoint: artifact or cache extraction for a job consuming attacker-produced archive content
- Attacker controls: archive bytes, entry names, path separators, absolute paths, `..` segments, links, and metadata, hidden names, dotfiles, and special prefixes
- Exploit idea: hide dangerous paths behind filtering assumptions
- Invariant to test: restore filters must treat hidden and special names with the same containment rules
- Expected Immunefi impact: secret exposure or trusted-config overwrite
- Fast validation: restore hidden control-style names and confirm they are blocked or isolated
