### Title
Zip extraction path traversal allows writing/chmodding files outside the job workspace - ([File: helpers/archives/zip_extract.go])

### Summary
`extractZipFile` and `ExtractZipArchive` in `helpers/archives/zip_extract.go` use `file.Name` from a zip archive entry directly, with no validation that the resulting path stays within the intended extraction root. An attacker-controlled zip (cache or artifact) with an entry named e.g. `../../evil.sh` will have its parent directories created via `os.MkdirAll(filepath.Dir(file.Name), ...)`, the file written via `os.OpenFile(file.Name, ...)`, and then `lchmod(file.Name, file.Mode())` applied — all outside the job's working directory.

### Finding Description
`extractZipFile` (helpers/archives/zip_extract.go:61-83) computes the destination purely from the attacker-controlled `file.Name`: [1](#0-0) 
There is no `filepath.Clean` + prefix check against a base/root directory, unlike the sibling tar+zstd extractor which explicitly validates: [2](#0-1) 
The only pre-write check performed is `errorIfGitDirectory`, which only rejects entries whose *cleaned* first path component is `.git` — it does nothing to reject `..`-prefixed traversal paths: [3](#0-2) 
After every file entry is written, `ExtractZipArchive` iterates again and calls `lchmod(file.Name, file.Mode())` using the same unvalidated `file.Name`, which will chmod the file at the traversed path, including setting execute bits if the zip's mode says so: [4](#0-3) 
This is a classic "Zip Slip" vulnerability. `ExtractZipArchive`/`ExtractZipFile` are used by the runner's `extract` helper subcommand which is invoked to unpack caches and artifacts uploaded by the job; a pipeline author controls the contents of the cache/artifact archive that is later extracted by the runner (potentially in a different job/stage, or the same job on re-run) via `CreateZipArchive` → upload → `ExtractZipArchive` → `extractZipFile` → `lchmod`. There is no path-confinement check anywhere in this call chain comparable to the one present in the tar+zstd extractor, so nothing stops a `../` entry from escaping the extraction directory.

### Impact Explanation
An unprivileged pipeline author who controls cache/artifact zip contents can cause the runner process to write files outside the job's own working directory (relative to wherever the extraction command's current working directory is set, e.g., the builds root) and can additionally flip the executable bit on that written file via `lchmod`. Depending on the runner's directory layout and extraction cwd, this could write/execute-mark a file in a sibling job/project directory or elsewhere in the builds hierarchy that the runner process has write access to — violating the "file operations must stay within intended build/cache/artifact roots" invariant. This does not by itself guarantee remote code execution (the written script isn't automatically executed), but it is a concrete file-write/chmod-outside-workspace primitive that can be chained with any other mechanism that later executes files under the builds directory.

### Likelihood Explanation
Fully feasible with attacker-only capabilities: a normal CI job can create a cache or artifact with a crafted zip file (using standard zip libraries, bypassing `CreateZipArchive`'s own logic entirely since the archive is just a zip file format) containing an entry such as `../../other-job/generated_script.sh` or similar relative traversal. When the runner later extracts that cache/artifact (`ExtractZipFile`/`ExtractZipArchive`), the vulnerable code path is directly and reliably reached — this is deterministic, not probabilistic, and repeatable on every run of the affected runner version.

### Recommendation
In `extractZipFile` (and `extractZipDirectoryEntry`/`extractZipSymlinkEntry`/`extractZipFileEntry`), resolve `file.Name` against the extraction root, `filepath.Clean` it, and verify with `filepath.Rel`/`strings.HasPrefix` (as already done in `commands/helpers/archive/tarzstd/tarzstd_extractor.go:57-64`) that the resulting absolute path stays within the root before any `os.MkdirAll`, `os.OpenFile`, `os.Symlink`, or `lchmod` call; reject (or skip with a warning, consistent with existing `.git` handling) any entry that resolves outside the root.

### Proof of Concept
```go
func TestExtractZipFile_PathTraversal(t *testing.T) {
    testOnArchive(t, func(t *testing.T, archive *zip.Writer) {
        f, err := archive.Create("../evil.sh")
        require.NoError(t, err)
        _, err = io.WriteString(f, "#!/bin/sh\necho pwned\n")
        require.NoError(t, err)
    }, func(t *testing.T, fileName string) {
        err := ExtractZipFile(fileName)
        require.NoError(t, err) // currently succeeds silently

        // Assert traversal did NOT happen (this should pass once fixed, fails today)
        _, statErr := os.Stat(filepath.Join(filepath.Dir(mustAbs(fileName)), "..", "evil.sh"))
        assert.True(t, os.IsNotExist(statErr), "expected extraction to be confined to root, but ../evil.sh was created")
    })
}
```
Expected today: the file `../evil.sh` is created relative to the extraction cwd (outside the intended root) and `lchmod` runs against it — demonstrating the traversal. After a fix enforcing root confinement, `ExtractZipArchive` should return an error (or safely skip) for such an entry, and `os.Stat` on the traversal target should report "not exist".

### Citations

**File:** helpers/archives/zip_extract.go (L61-66)
```go
func extractZipFile(file *zip.File) (err error) {
	// Create all parents to extract the file
	err = os.MkdirAll(filepath.Dir(file.Name), 0o777)
	if err != nil {
		return err
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

**File:** commands/helpers/archive/tarzstd/tarzstd_extractor.go (L57-64)
```go
		var path string
		path, err = filepath.Abs(filepath.Join(e.dir, hdr.Name))
		if err != nil {
			return err
		}
		if !strings.HasPrefix(path, e.dir+string(filepath.Separator)) && path != e.dir {
			return fmt.Errorf("%s cannot be extracted outside of chroot (%s)", path, e.dir)
		}
```

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
