Confirmed by direct code review: [1](#0-0)  `errorIfGitDirectory` never blocks anything — it only *constructs* a `*os.PathError` when the top-level path component of an archive entry is exactly `.git`. [2](#0-1)  In `ExtractZipArchive`, the return value of `errorIfGitDirectory` is fed into `tracker.actionable(err)` purely to decide whether to print a de-duplicated warning log line; the loop then unconditionally calls `extractZipFile(file)` on the very same file regardless of the git-directory check's outcome. There is no `continue`/`return` on a `.git` hit. [3](#0-2)  `extractZipFile`/`extractZipFileEntry` write the file with `file.Mode().Perm()`, so a zip's stored Unix permission bits (including the executable bit) are honored — an attacker can ship an executable `.git/hooks/post-checkout` (or any other hook) inside the archive. [4](#0-3)  Additionally, `isPathAGitDirectory` only flags the case where `.git` is the *first* path component (`parts[0] == ".git"`), confirmed by the existing test table where `test/.git` and `test/.git/test` are explicitly marked `unsafe: false`. [5](#0-4)  So even the warning itself is easily bypassed for nested repos/subdirectories, on top of being non-blocking.

Given these confirmed facts, the described exploit primitive is real: a crafted cache/artifact zip containing `.git/hooks/<hook-name>` will be written into the workspace's real `.git/hooks` directory during `ExtractZipArchive`/`ExtractZipFile`, with no abort and only (sometimes) a log warning. Whether this converts into "runner-side command execution" downstream depends on whether the job's own subsequent git operations (`checkout`, `pull`, `commit`, etc., invoked by the job's generated shell script) execute that hook — which is standard, unmodified git behavior for hooks placed in `$GIT_DIR/hooks`, and I found no evidence in this repository of `core.hooksPath` being overridden or hooks being disabled during job git operations to prevent this.

### Title
Advisory-only `.git` directory check lets crafted zip archives inject executable git hooks during extraction - (File: helpers/archives/zip_extract.go, helpers/archives/path_check_helper.go)

### Summary
`errorIfGitDirectory` produces a `*os.PathError` used only for warning/log deduplication, never to stop extraction, so `ExtractZipArchive` writes `.git/**` entries (including `.git/hooks/*`) from an attacker-controlled cache/artifact zip straight into the workspace's live git repository, permissions and all. Because the detection itself only matches `.git` as the first path segment, it also misses nested repositories entirely.

### Finding Description
`isPathAGitDirectory` (path_check_helper.go:13-19) checks only `parts[0] == ".git"`. `errorIfGitDirectory` (path_check_helper.go:21-31) wraps a hit into a `*os.PathError` but the caller in `ExtractZipArchive` (zip_extract.go:88-96) uses that solely to gate a `printGitArchiveWarning` log call via `tracker.actionable(err)`; there is no early `continue`/abort. The very next statement in the same loop iteration unconditionally calls `extractZipFile(file)` (zip_extract.go:93, referencing 41-59), which creates parent dirs and writes the file content with the archive's stored mode bits, including the executable bit for hook scripts. An attacker who controls a job's cache/artifact upload (e.g., stage 1 job in the same pipeline) can therefore ship `.git/hooks/post-checkout`, `.git/hooks/pre-commit`, etc., inside the zip. When a later stage restores that cache/artifact into a workspace that already contains a real `.git` directory (the normal state after GitLab Runner's source checkout), the hook file is written into the live repo's hooks path. Any subsequent `git` invocation from the job's generated shell script that triggers that hook (checkout → `post-checkout`, commit → `pre-commit`, etc.) executes attacker-controlled code in the context of that later job.

### Impact Explanation
This allows an unprivileged pipeline author to plant an executable payload in an early-stage artifact/cache and have it execute inside a later stage's job execution context via a normal git hook trigger, without any code review/approval step recognizing it as an executable payload (it's hidden inside `.git/hooks`). In pipelines where later stages run with different/elevated CI/CD variables, protected-branch secrets, or deploy credentials than the stage that produced the artifact, this provides a path to leak or abuse those elevated credentials from a lower-trust stage — a genuine cross-stage privilege boundary violation within the job execution sandbox.

### Likelihood Explanation
Preconditions are realistic and fully attacker-controlled: any pipeline author can control artifact/cache contents via `.gitlab-ci.yml` and job scripts (e.g., `zip -r artifact.zip .git`, or a custom zip build step), and cache/artifact restoration into a workspace with a pre-existing `.git` is the default operation for `git`/`git-checkout` clone strategies. The only barrier is a non-blocking warning log line, which most pipelines never inspect. This is reliably repeatable and does not require any privileged/admin misconfiguration.

### Recommendation
Make `errorIfGitDirectory`'s result actually gate extraction: when the check flags a `.git`-rooted entry, `ExtractZipArchive` (and the analogous tar path) should skip writing that entry (or abort the whole extraction, matching a strict "no `.git` writes" invariant) instead of only conditionally logging. Also fix `isPathAGitDirectory` to detect `.git` anywhere in the path (any nested repo), not just as the first path component, closing the `test/.git/...` bypass demonstrated by the existing test table.

### Proof of Concept
Go test in `helpers/archives`:
1. Build an in-memory `zip.Writer` with one entry named `.git/hooks/post-checkout` containing a shell payload (e.g., `#!/bin/sh\ntouch /tmp/pwned`) and mode `0755`.
2. Call `archives.ExtractZipArchive` against a `zip.Reader` of that buffer, extracting into a temp dir that already has a `.git` directory (e.g., from `git init`).
3. Assert `os.Stat(filepath.Join(tmpDir, ".git/hooks/post-checkout"))` succeeds and the file is executable — proving the "advisory-only" check did not prevent the write.
4. Optionally, run `git -C tmpDir checkout HEAD` (or `git commit --allow-empty` for a `pre-commit` variant) and assert `/tmp/pwned` was created, confirming hook execution off the extracted archive content.

### Citations

**File:** helpers/archives/path_check_helper.go (L13-19)
```go
func isPathAGitDirectory(path string) bool {
	parts := strings.Split(filepath.Clean(path), string(filepath.Separator))
	if len(parts) > 0 && parts[0] == ".git" {
		return true
	}
	return false
}
```

**File:** helpers/archives/path_check_helper.go (L21-31)
```go
func errorIfGitDirectory(path string) *os.PathError {
	if !isPathAGitDirectory(path) {
		return nil
	}

	return &os.PathError{
		Op:   ".git inside of archive",
		Path: path,
		Err:  errors.New("trying to archive or extract .git path"),
	}
}
```

**File:** helpers/archives/zip_extract.go (L41-59)
```go
func extractZipFileEntry(file *zip.File) (err error) {
	var out *os.File
	in, err := file.Open()
	if err != nil {
		return err
	}
	defer func() { _ = in.Close() }()

	// Remove file before creating a new one, otherwise we can error that file does exist
	_ = os.Remove(file.Name)
	out, err = os.OpenFile(file.Name, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, file.Mode().Perm())
	if err != nil {
		return err
	}
	defer func() { _ = out.Close() }()
	_, err = io.Copy(out, in)

	return
}
```

**File:** helpers/archives/zip_extract.go (L88-96)
```go
	for _, file := range archive.File {
		if err := errorIfGitDirectory(file.Name); tracker.actionable(err) {
			printGitArchiveWarning("extract")
		}

		if err := extractZipFile(file); tracker.actionable(err) {
			logrus.Warningf("%s: %s (suppressing repeats)", file.Name, err)
		}
	}
```

**File:** helpers/archives/path_check_helper_test.go (L12-25)
```go
func TestDoesPathsListContainGitDirectory(t *testing.T) {
	examples := []struct {
		path   string
		unsafe bool
	}{
		{".git", true},
		{".git/", true},
		{"././././.git/", true},
		{"././.git/.././.git/", true},
		{".git/test", true},
		{"./.git/test", true},
		{"test/.git", false},
		{"test/.git/test", false},
	}
```
