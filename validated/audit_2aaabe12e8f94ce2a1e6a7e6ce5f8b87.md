### Title
Zip extraction path traversal ("zip-slip") allows writing outside the extraction root - (File: helpers/archives/zip_extract.go)

### Summary
`extractZipFile` and its callers use the raw `file.Name` from a zip entry with no path traversal sanitization before calling `os.MkdirAll(filepath.Dir(file.Name), 0o777)`, `os.Mkdir`, `os.OpenFile`, and `os.Symlink`. A malicious cache/artifact zip with entries like `../../etc/cron.d/evil` will create directories and files outside the intended extraction root, with the reachable `MkdirAll` call using the permissive `0o777` mode (subject only to umask).

### Finding Description
The only validation performed during extraction is `errorIfGitDirectory`, which checks whether the first path segment is `.git` — it does nothing to reject `..` traversal segments. [1](#0-0)  `ExtractZipArchive` iterates the zip's file list and calls `extractZipFile(file)` directly with `file.Name`, only checking the git-directory case, with no root-confinement check. [2](#0-1) 

Inside `extractZipFile`, the parent directories are created straight from `filepath.Dir(file.Name)` with `os.MkdirAll(..., 0o777)`, and depending on entry type the code proceeds to `os.Mkdir`, `os.Symlink`, or `os.OpenFile` using the unmodified `file.Name`: [3](#0-2)  None of `extractZipDirectoryEntry`, `extractZipSymlinkEntry`, or `extractZipFileEntry` clean or confine the path either. [4](#0-3) 

This code is reachable from the cache/artifact zip extractor: `ziplegacy.extractor.Extract` calls `archives.ExtractZipArchive(zr)` directly on a zip built from job/pipeline-controlled artifact or cache content. [5](#0-4)  Since cache archives are downloaded and produced based on job-controlled `cache:paths`/artifact content and extracted by the runner (e.g., in the shell executor's cache/artifact restore flow, which later runs a `chown -R` on the cache directory), a crafted entry name such as `../../../../etc/cron.d/evil` will cause `os.MkdirAll` to create `/etc/cron.d` (or any other absolute-relative-escaping path reachable from the working directory) with `0o777` permissions before the file itself is written, entirely outside the designated cache/workspace root.

### Impact Explanation
An attacker who controls the contents of a cache or artifact zip (any CI job author) can cause the runner process to create arbitrary directories/files outside the job's workspace/cache root, with world-writable directory permissions (`0o777`, only limited by umask) along the traversal path. Depending on the runner user's privileges and the specific OS/user context, this can plant files in sensitive locations (e.g., cron directories, shell profile files, or other locations that a later trusted operation — such as `chown -R` over the cache root — might touch), which matches the described privilege/permission-escalation impact via files/directories placed outside the intended job root.

### Likelihood Explanation
This is a classic and straightforward "zip-slip" style vulnerability: no code path validates or confines `file.Name` before use in filesystem operations. Any pipeline author able to push a crafted cache or artifact zip (fully within the "unprivileged CI job author" threat model) can trigger it deterministically and repeatably; the only limiting factor is the OS-level permission of the process performing extraction (which determines whether traversal outside typical build directories succeeds), not any Runner-side defense — there is none.

### Recommendation
Before performing any filesystem operation for a zip entry, clean and validate `file.Name` (e.g., via `filepath.Clean` and rejecting entries whose resolved path, when joined to the extraction root, does not have the root as a prefix — i.e., reject `..` segments and absolute paths). Apply this validation once per entry in `ExtractZipArchive`/`extractZipFile` before calling `os.MkdirAll`, `os.Mkdir`, `os.OpenFile`, or `os.Symlink`, and additionally avoid the overly permissive `0o777` mode for created parent directories (use a restrictive default such as `0o755`, subject to umask).

### Proof of Concept
Go unit test in `helpers/archives/zip_extract_test.go`:
1. Create a temporary directory `root` and `os.Chdir` into it (or pass a target dir if the API supports it).
2. Build an in-memory zip (`archive/zip`) with a single entry named `../../../../tmp/zipslip-poc/evil.txt` (or, on a fixed test root, a name with enough `../` segments to escape `root`).
3. Call `archives.ExtractZipArchive` on the reader.
4. Assert that `tmp/zipslip-poc/evil.txt` does NOT exist outside `root`, and that no directory was created outside `root`.
5. Currently, this assertion fails: the file is written outside `root`, and the created parent directory shows `0o777`-derived permissions (masked only by umask), proving the vulnerability.

### Citations

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

**File:** helpers/archives/zip_extract.go (L12-59)
```go
func extractZipDirectoryEntry(file *zip.File) (err error) {
	err = os.Mkdir(file.Name, file.Mode().Perm())

	// The "directory does exist" error is not an error for us
	if os.IsExist(err) {
		err = nil
	}
	return
}

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

**File:** helpers/archives/zip_extract.go (L61-83)
```go
func extractZipFile(file *zip.File) (err error) {
	// Create all parents to extract the file
	err = os.MkdirAll(filepath.Dir(file.Name), 0o777)
	if err != nil {
		return err
	}

	switch file.Mode() & os.ModeType {
	case os.ModeDir:
		err = extractZipDirectoryEntry(file)

	case os.ModeSymlink:
		err = extractZipSymlinkEntry(file)

	case os.ModeNamedPipe, os.ModeSocket, os.ModeDevice:
		// Ignore files of these types
		logrus.Warningf("File ignored: %q", file.Name)

	default:
		err = extractZipFileEntry(file)
	}
	return
}
```

**File:** helpers/archives/zip_extract.go (L85-96)
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

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L24-32)
```go
// Extract extracts files from the reader to the directory passed to
// NewZipExtractor.
func (e *extractor) Extract(ctx context.Context) error {
	zr, err := zip.NewReader(e.r, e.size)
	if err != nil {
		return err
	}

	return archives.ExtractZipArchive(zr)
```
