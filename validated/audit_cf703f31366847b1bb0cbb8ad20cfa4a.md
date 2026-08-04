### Title
Unvalidated symlink target and missing path traversal check in zip artifact extraction allows arbitrary symlink creation - (File: helpers/archives/zip_extract.go)

### Summary
`extractZipSymlinkEntry` in `helpers/archives/zip_extract.go` creates a symlink from a zip entry's Name and file content without any validation of either value, and the calling loop in `ExtractZipArchive` only checks for `.git` paths, not path traversal or absolute paths. A malicious artifact/cache zip processed by the legacy zip extractor can therefore create a dangling or absolute symlink pointing outside the job workspace.

### Finding Description
`extractZipSymlinkEntry` reads the symlink target from the zip entry contents via `file.Open()`/`io.ReadAll` and calls `os.Symlink(string(data), file.Name)` directly: [1](#0-0) 

Neither the target (`data`) nor the link name (`file.Name`) is checked for absolute paths or `..` traversal components. The only validation performed anywhere in the extraction loop is a `.git` directory check, which does nothing to prevent path traversal or arbitrary symlink targets: [2](#0-1) [3](#0-2) 

This code path is exercised by `ExtractZipFile`/`ExtractZipArchive`, which are invoked from the `ziplegacy` zip extractor implementation (`commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`), one of the zip extraction backends available to artifact/cache extraction alongside `fastzip`. An unprivileged pipeline author fully controls the contents of artifacts/cache archives uploaded from their own job, including arbitrary zip entry names and symlink target data, so this is directly attacker-reachable input.

Because there is no `filepath.Clean`/`filepath.Rel`/prefix check ensuring the resolved symlink stays within the extraction root, a crafted entry named `evil` with mode `os.ModeSymlink` and content `/etc/shadow` (or `../other-project/secret`) will result in `os.Symlink("/etc/shadow", "evil")` being created verbatim in the extraction directory.

### Impact Explanation
Creating the symlink itself does not immediately read or write the target file, but it plants a dangling/absolute symlink inside the job workspace. Any subsequent step in the same job or pipeline that dereferences that path without `O_NOFOLLOW` semantics (e.g. a later script step reading the "extracted file", or a cache/artifact archiver that follows symlinks when re-packaging output) can be tricked into reading or overwriting a file outside the intended build/artifact root, violating the invariant that file operations stay within the job's designated roots.

### Likelihood Explanation
This requires no special privileges beyond running a normal CI job that produces/uploads a crafted zip artifact or cache archive, and requires the `ziplegacy` extraction path to be the one used to extract it (as opposed to `fastzip`, which appeared to contain its own path-validation logic based on repo grep results — this could not be fully confirmed from available context). Given that `ziplegacy` exists as a supported extractor and directly reuses this unmodified legacy code, the exploit is straightforward and fully repeatable whenever that code path is taken.

### Recommendation
In `extractZipSymlinkEntry` and in the file-entry write path, validate that:
- `file.Name` resolves (via `filepath.Clean`/`filepath.Rel` against the extraction root) to a path within the extraction root, rejecting absolute paths and `..` traversal, and
- the symlink target `data` does not escape the extraction root either (reject absolute targets and traversal that resolves outside the root), consistent with standard zip-slip mitigations.

### Proof of Concept
Go unit test sketch for `helpers/archives`:
```go
func TestExtractZipArchive_RejectsMaliciousSymlink(t *testing.T) {
    dir := t.TempDir()
    zipPath := filepath.Join(dir, "evil.zip")
    // build a zip with one entry "evil" with mode os.ModeSymlink and content "/etc/shadow"
    // ... (write via archive/zip Writer, setting header.SetMode(os.ModeSymlink|0777))

    require.NoError(t, os.Chdir(dir))
    err := archives.ExtractZipFile(zipPath)
    require.NoError(t, err)

    linkPath := filepath.Join(dir, "evil")
    target, err := os.Readlink(linkPath)
    require.NoError(t, err)
    // Assertion that currently FAILS, proving the bug:
    assert.NotEqual(t, "/etc/shadow", target, "extractor must not create symlink to absolute host path")
}
```
Expected result today: the assertion fails because `os.Symlink("/etc/shadow", "evil")` is created unconditionally, confirming the missing validation.

### Citations

**File:** helpers/archives/zip_extract.go (L22-39)
```go
func extractZipSymlinkEntry(file *zip.File) (err error) {
	var data []byte
	in, err := file.Open()
	if err != nil {
		return err
	}
	defer func() { _ = in.Close() }()

	data, err = io.ReadAll(in)
	if err != nil {
		return err
	}

	// Remove symlink before creating a new one, otherwise we can error that file does exist
	_ = os.Remove(file.Name)
	err = os.Symlink(string(data), file.Name)
	return
}
```

**File:** helpers/archives/zip_extract.go (L85-97)
```go
func ExtractZipArchive(archive *zip.Reader) error {
	tracker := newPathErrorTracker()

	for _, file := range archive.File {
		if err := errorIfGitDirectory(file.Name); tracker.actionable(err) {
			printGitArchiveWarning("extract")
		}

		if err := extractZipFile(file); tracker.actionable(err) {
			logrus.Warningf("%s: %s (suppressing repeats)", file.Name, err)
		}
	}

```

**File:** helpers/archives/path_check_helper.go (L13-31)
```go
func isPathAGitDirectory(path string) bool {
	parts := strings.Split(filepath.Clean(path), string(filepath.Separator))
	if len(parts) > 0 && parts[0] == ".git" {
		return true
	}
	return false
}

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
