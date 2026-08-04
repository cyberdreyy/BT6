### Title
Cache/artifact zip extraction into `.git/` allows overwrite of `.git/hooks/*`, enabling hook execution on subsequent git commands within the job - ([File: helpers/archives/zip_extract.go])

### Summary
`ExtractZipArchive` calls `errorIfGitDirectory` only to print a warning via `printGitArchiveWarning`, but does not stop or skip extraction of the offending entry — `extractZipFile(file)` runs unconditionally afterward. This lets an attacker-controlled cache/artifact zip write arbitrary content (including executable hook scripts) into `.git/hooks/`, which will be executed the next time git runs a corresponding hook (e.g., `post-checkout`, `pre-commit`) inside the same job.

### Finding Description
In `helpers/archives/zip_extract.go`, `ExtractZipArchive` iterates the archive entries: [1](#0-0) 

`errorIfGitDirectory` in `helpers/archives/path_check_helper.go` detects a `.git/...` path and returns a `*os.PathError`, but the caller only uses this to log a warning via `printGitArchiveWarning`; it never `continue`s the loop or otherwise skips `extractZipFile`: [2](#0-1) 

This is confirmed by `TestExtractZipFileWithGitPath`, which explicitly asserts that after extraction, `.git/test_file` exists on disk despite the warning being logged: [3](#0-2) 

`extractZipFile`/`extractZipFileEntry` write file content and preserve/apply mode bits (via `lchmod` later in the same loop over `archive.File`), so a `.git/hooks/post-checkout` entry with executable permission bits in the zip would be written out with those permissions: [4](#0-3) [5](#0-4) 

Cache and artifact extraction in the Runner both funnel through `ExtractZipFile`/`ExtractZipArchive` for zip-format archives, so any job that restores a cache or downloads an artifact from an earlier stage where an attacker (the pipeline author, or a compromised earlier job/dependency) controls the zip content can plant files under `.git/hooks/`. If the job later runs any `git` command that fires the matching hook (which is a normal thing to happen in "get sources" or user script `git` invocations), the hook executes.

### Impact Explanation
This is a genuine write-then-exec primitive scoped entirely to the job's own workspace: an attacker who can influence cache/artifact zip contents for a job (e.g., via a shared cache key, a prior stage's artifact they control, or their own job) can get arbitrary command execution in a *later stage of the same job/pipeline*, without any executor-level check preventing it. While the executed code still runs inside the same job's sandbox (same privileges the job already has), it crosses the intended trust boundary described by the code's own warning ("Part of .git directory is on the list of files to extract" / "This may introduce unexpected problems") — the mechanism meant to flag this risk does not actually block it, so the warning is misleading and gives false assurance. It's a real logic bug (dead/no-op enforcement) even though the scoped impact is limited to code execution within the job's own workspace/sandbox, not cross-job or cross-project escalation.

### Likelihood Explanation
Preconditions are realistic and fully attacker-reachable without any privilege beyond that of a normal pipeline author: control an artifact or cache zip consumed later in the same job or pipeline (e.g., define a job that produces a "poisoned" artifact containing `.git/hooks/post-checkout`, then a downstream job in the same pipeline that fetches this artifact before running git commands). No special executor configuration or admin privilege is required. The behavior is deterministically reproducible per `TestExtractZipFileWithGitPath`.

### Recommendation
Change `ExtractZipArchive` to actually skip extraction of `.git/*` paths when `errorIfGitDirectory` returns a non-nil error, e.g. `continue` to the next archive entry instead of merely logging, or make this fail-closed (abort extraction) rather than fail-open. The same fix should be applied symmetrically to any other archive extractors (e.g., tar) and to the legacy zip extractor at `commands/helpers/archive/ziplegacy/zip_legacy_extractor.go` if they share the same pattern.

### Proof of Concept
Extend `TestExtractZipFileWithGitPath` to prove executability:
1. Build a zip archive containing an entry `.git/hooks/post-checkout` with mode `0o755` and shell content (e.g., `#!/bin/sh\ntouch pwned`).
2. Call `ExtractZipArchive`/`ExtractZipFile` against a temp working directory that already has a `.git` repo initialized.
3. Assert `.git/hooks/post-checkout` exists and is executable (`os.Stat(...).Mode()&0o111 != 0`).
4. Run `git checkout -b test` (or any command that triggers `post-checkout`) in that directory and assert the `pwned` marker file was created, proving hook execution.
5. Assert the warning log line is present ("Part of .git directory is on the list of files to extract") while extraction still proceeded — showing the warning is decorative, not a control.

### Citations

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

**File:** helpers/archives/zip_extract.go (L98-107)
```go
	for _, file := range archive.File {
		if err := lchmod(file.Name, file.Mode()); tracker.actionable(err) {
			logrus.Warningf("%s: %s (suppressing repeats)", file.Name, err)
		}

		// Process zip metadata
		if err := processZipExtra(&file.FileHeader); tracker.actionable(err) {
			logrus.Warningf("%s: %s (suppressing repeats)", file.Name, err)
		}
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

**File:** helpers/archives/zip_extract_test.go (L69-92)
```go
func TestExtractZipFileWithGitPath(t *testing.T) {
	testOnArchive(t, createArchiveWithGitPath, func(t *testing.T, fileName string) {
		output := logrus.StandardLogger().Out
		var buf bytes.Buffer
		logrus.SetOutput(&buf)
		defer logrus.SetOutput(output)

		err := ExtractZipFile(fileName)
		require.NoError(t, err)

		assert.Contains(t, buf.String(), "Part of .git directory is on the list of files to extract")

		stat, err := os.Stat(".git/test_file")
		assert.False(t, os.IsNotExist(err), "Expected .git/test_file to exist")
		if !os.IsNotExist(err) {
			assert.NoError(t, err)
		}

		if stat != nil {
			defer os.Remove(".git/test_file")
			assert.Equal(t, int64(13), stat.Size())
		}
	})
}
```
