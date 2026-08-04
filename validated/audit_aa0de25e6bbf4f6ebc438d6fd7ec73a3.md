Confirmed finding: the zip-based extraction path (`ExtractZipArchive` in `helpers/archives/zip_extract.go`, used by the legacy zip extractor `commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`, which is invoked from the `artifacts-downloader` helper after `network/gitlab.go`'s `DownloadArtifacts` writes the archive bytes) only **warns** about `.git`-prefixed entries via `errorIfGitDirectory`/`printGitArchiveWarning` and does **not** block extraction of them (`helpers/archives/zip_extract.go:88-96`, `helpers/archives/path_check_helper.go:21-31`). This means an attacker-controlled artifact zip can legitimately overwrite files under the consuming job's `.git/hooks/` directory (e.g. `pre-commit`, `pre-push`, `post-checkout`) as regular/executable/symlink zip entries, since `extractZipFile` (`helpers/archives/zip_extract.go:41-59`) sets the file mode straight from the zip entry and `extractZipSymlinkEntry` (lines 22-39) creates arbitrary symlinks with no destination validation.

### Title
Artifact zip extraction only warns—never blocks—writes into `.git/` (including `.git/hooks/`), allowing a dependency artifact to plant executable git hooks - (File: helpers/archives/zip_extract.go, commands/helpers/archive/ziplegacy/zip_legacy_extractor.go)

### Summary
`DownloadArtifacts` in `network/gitlab.go` streams attacker-influenced artifact bytes to disk, which are then extracted by `ArtifactsDownloaderCommand.Execute` (`commands/helpers/artifacts_downloader.go:88-141`) into the job's working directory. When the legacy zip codepath is used, `ExtractZipArchive` (`helpers/archives/zip_extract.go:85-110`) merely logs a warning for entries under `.git/` instead of rejecting them, so a crafted artifact zip can write/overwrite git hook scripts (`.git/hooks/pre-commit`, `pre-push`, etc.) with executable permissions.

### Finding Description
- `errorIfGitDirectory` (`helpers/archives/path_check_helper.go:21-31`) detects a `.git`-prefixed path and returns a `*os.PathError`, but the caller only treats it as a warning:
  ```go
  if err := errorIfGitDirectory(file.Name); tracker.actionable(err) {
      printGitArchiveWarning("extract")
  }
  ```
  `tracker.actionable` logs/rate-limits the message but never causes `ExtractZipArchive` to `continue`/skip the entry or abort — `extractZipFile(file)` is still called immediately for every entry regardless of the git-directory check result (`helpers/archives/zip_extract.go:88-96`).
- `extractZipFileEntry` (lines 41-59) and `extractZipDirectoryEntry`/`extractZipSymlinkEntry` write the file at `file.Name` with the mode taken directly from the zip header (`file.Mode().Perm()`), and `lchmod` is later applied per the archive's stored mode (line 99), so an attacker can mark a `.git/hooks/*` entry as executable.
- `extractZipSymlinkEntry` (lines 22-39) creates a symlink at `file.Name` pointing to arbitrary attacker-supplied target data with zero destination validation.
- `BuildStageDownloadArtifacts` runs **after** `BuildStageGetSources` (`common/build.go:120-130`, `common/build.go:932-937`), and the `.git/hooks` cleanup (`writeGitCleanupAllConfigs` in `shells/abstract.go:1141-1171`, and the steps-mode equivalent `cleanupGitState` in `functions/concrete/run/stages/get_sources.go:555-592`) runs only during the earlier `GetSources` stage. There is no re-cleanup of `.git/hooks` after artifact download and before the user's `step_script`/`build_script` stage runs.
- Consequently, if a pipeline is configured with a `needs:`/dependency relationship where a later, more-privileged job downloads an earlier job's artifact, and that later job's own script subsequently invokes ordinary git commands (`git commit`, `git push`, `git checkout`, common in release/tag/deploy jobs), a maliciously restored git hook executes with that job's credentials/permissions — without any additional trust check being re-established between artifact restore and hook execution.
- The path-traversal ("zip slip") case for the *tarzstd* extractor is well-guarded (`commands/helpers/archive/tarzstd/tarzstd_extractor.go:57-64`) and file-archiver upload paths are validated (`commands/helpers/file_archiver_test.go` tests), but the `.git` restriction on the legacy-zip *extraction* path is advisory-only, not an enforced invariant.

### Impact Explanation
An attacker who can only control one job's artifact contents (job/pipeline author or an unprivileged contributor whose job artifact is consumed via `needs:` by a more-trusted downstream job) can achieve arbitrary command execution in the context of that downstream job merely by naming files `.git/hooks/pre-commit` (etc.) inside their artifact zip, provided the downstream job's own script performs a git operation that fires the corresponding hook. This is "stronger-context execution" as scoped — code execution in a job context with credentials/scope not directly granted to the artifact-producing job/user.

### Likelihood Explanation
Requires: (1) a pipeline with a dependency relation (`needs:`/`dependencies:`) between an attacker-influenced job and a more privileged job, (2) the downstream job's script performing a git operation after artifact download (common for deploy/release/tag automation), and (3) the legacy zip extractor being used (default `Zip` format unless `FF_USE_FASTZIP`/zstd path is selected — `openArchive` in `commands/helpers/artifacts_downloader.go:143-172` defaults to `archive.Zip`). This is feasible and fully attacker-controlled via ordinary CI configuration (artifact upload + `needs:`), no admin/leaked-key/cluster-compromise required.

### Recommendation
Make the `.git` path check in `ExtractZipArchive` a hard failure instead of a warning — skip/reject any entry whose name is under `.git/` (mirroring the fully-blocking behavior that should apply consistently across extractors), and/or explicitly re-run the existing `.git/hooks` and `.git/config` cleanup routine (`writeGitCleanupAllConfigs` / `cleanupGitState`) immediately after the `BuildStageDownloadArtifacts` stage completes, before any user script executes.

### Proof of Concept
Go unit test in `helpers/archives` package:
```go
func TestExtractZipArchive_RejectsGitHookOverwrite(t *testing.T) {
    dir := t.TempDir()
    chdir(t, dir)
    require.NoError(t, os.MkdirAll(".git/hooks", 0o755))

    buf := &bytes.Buffer{}
    zw := zip.NewWriter(buf)
    hdr := &zip.FileHeader{Name: ".git/hooks/pre-commit"}
    hdr.SetMode(0o755)
    w, _ := zw.CreateHeader(hdr)
    _, _ = w.Write([]byte("#!/bin/sh\ntouch /tmp/pwned\n"))
    require.NoError(t, zw.Close())

    zr, err := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    require.NoError(t, err)

    err = ExtractZipArchive(zr)
    require.Error(t, err) // currently fails: err is nil and the hook file is written

    _, statErr := os.Stat(".git/hooks/pre-commit")
    assert.True(t, os.IsNotExist(statErr), ".git/hooks/pre-commit should not have been created")
}
```
Expected (fixed) behavior: `ExtractZipArchive` returns an error (or silently skips) for `.git/`-prefixed entries and `.git/hooks/pre-commit` is never written. Currently the file is written with executable mode, confirming the vulnerability.