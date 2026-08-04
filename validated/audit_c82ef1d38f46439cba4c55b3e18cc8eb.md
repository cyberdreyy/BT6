### Title
Zip archive extraction allows path traversal via crafted `zip.File.Name` entries - (File: `helpers/archives/zip_extract.go`)

### Summary
`extractZipFile` (and its helpers `extractZipDirectoryEntry`, `extractZipSymlinkEntry`, `extractZipFileEntry`) in `helpers/archives/zip_extract.go` use `file.Name` from the zip archive directly with `os.MkdirAll(filepath.Dir(file.Name), ...)` and `os.OpenFile(file.Name, ...)`/`os.Mkdir`/`os.Symlink` without any validation that the resulting path stays within the intended extraction root. `ziplegacy.extractor.Extract` (`commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`) passes the raw `zip.Reader` straight to `archives.ExtractZipArchive` and never uses its own `dir` field to constrain or chroot the extraction, so a crafted cache/artifact zip with `../` segments in entry names can write files anywhere the runner process has permission to write, including outside the job workspace.

### Finding Description
The reachable path is: `CacheExtractorCommand.Execute` (`commands/helpers/cache_extractor.go:655-663`) calls `archive.NewExtractor(format, f, size, wd)` and then `extractor.Extract(ctx)`. For the zip legacy format this resolves to `ziplegacy.extractor.Extract` (`commands/helpers/archive/ziplegacy/zip_legacy_extractor.go:26-33`), which builds a `zip.Reader` and calls `archives.ExtractZipArchive(zr)` [1](#0-0) . Note the `extractor` struct stores `dir` (the working directory passed by the caller) but `Extract` never uses `e.dir` — it does not `os.Chdir` into it, nor does it join/validate `file.Name` against it [2](#0-1) .

`ExtractZipArchive` iterates `archive.File` and, for each entry, only checks `errorIfGitDirectory(file.Name)` (which only detects a literal `.git` first path segment, not `..` traversal) before calling `extractZipFile(file)` [3](#0-2) . `extractZipFile` then does:
```go
err = os.MkdirAll(filepath.Dir(file.Name), 0o777)
...
out, err = os.OpenFile(file.Name, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, file.Mode().Perm())
``` [4](#0-3) 
No call to `filepath.Clean`, no verification that the cleaned path is still a descendant of the extraction root, and no rejection of names containing `..` segments. The same lack of validation applies to `extractZipSymlinkEntry` (`os.Symlink(string(data), file.Name)`) and `extractZipDirectoryEntry` (`os.Mkdir(file.Name, ...)`) [5](#0-4) .

Because `zip.File.Name` is fully attacker-controlled (an unprivileged job author builds their own cache/artifact archive uploaded via `cache-archiver`/`artifacts-uploader`, later restored by `cache-extractor`/`artifacts-downloader`), an entry such as `../../../var/run/secrets/kubernetes.io/serviceaccount/token` will cause `filepath.Dir(file.Name)` to resolve outside the extraction working directory, and `os.OpenFile` will truncate/overwrite that file if the runner process has write permission to it (e.g., a writable projected service-account token file, a shared volume mount, or another path within the executor's filesystem namespace that is writable by the job's OS user).

### Impact Explanation
If the target executor mounts a writable path reachable via traversal from the job workspace (e.g., a Kubernetes executor pod where the workspace and a mounted service-account-token volume/emptyDir share a writable filesystem, or a shell/docker executor where the job user can write to parent directories), an unprivileged job can overwrite that file. Overwriting a service-account token grants the job a stronger identity/permission set than the executor configured. Overwriting a shared volume file (e.g., a config file, a state file, or a secret) can corrupt or escalate the job's access to other workloads or the host. This violates the invariant that job-controlled archives must not cause file access outside the workspace.

### Likelihood Explanation
**Preconditions:**
1. Attacker controls cache/artifact zip content (normal job author uploading a cache or artifact).
2. The runner process has write permission to a path outside the job workspace that is reachable via `../` traversal from the workspace (common in Kubernetes executors with mounted volumes, or shell executors on shared hosts).
3. The zip archive is extracted via `cache-extractor` or `artifacts-downloader` (standard flow).

**Feasibility:** Very high. The zip format allows arbitrary `Name` fields; no signature or integrity check prevents a job author from crafting a malicious zip. The extraction code has no path validation whatsoever.

**Repeatability:** Deterministic. Any zip with a traversal entry will trigger the vulnerability every time it is extracted.

### Recommendation
Validate and sanitize `file.Name` before any file operation. Specifically:
1. Clean the path: `cleanedName := filepath.Clean(file.Name)`
2. Reject absolute paths: `if filepath.IsAbs(cleanedName) { skip or error }`
3. Reject traversal: `if strings.Contains(cleanedName, "..")` or use `filepath.Rel(extractionRoot, filepath.Join(extractionRoot, cleanedName))` and verify the result is still under `extractionRoot`.
4. Ensure `ziplegacy.extractor.Extract` uses its `dir` field to constrain extraction (e.g., `os.Chdir(e.dir)` before extraction, or join all paths with `e.dir`).

Alternatively, use a library that performs this validation (e.g., `archive/tar` with `filepath.Join` and `filepath.Rel` checks, or a third-party zip extractor with built-in path sanitization).

### Proof of Concept
**Go unit test for `ExtractZipArchive`:**

```go
package archives

import (
	"archive/zip"
	"bytes"
	"io"
	"os"
	"path/filepath"
	"testing"
)

func TestExtractZipArchivePathTraversal(t *testing.T) {
	// Create a temporary directory for extraction
	tmpDir := t.TempDir()
	extractDir := filepath.Join(tmpDir, "extract")
	os.Mkdir(extractDir, 0o755)
	
	// Create a temporary file outside the extraction directory
	outsideDir := filepath.Join(tmpDir, "outside")
	os.Mkdir(outsideDir, 0o755)
	targetFile := filepath.Join(outsideDir, "target.txt")
	os.WriteFile(targetFile, []byte("original"), 0o644)
	
	// Create a zip archive with a path traversal entry
	// The entry name is "../outside/target.txt" relative to extractDir
	buf := new(bytes.Buffer)
	zw := zip.NewWriter(buf)
	
	// Add a file with a traversal path
	w, err := zw.Create("../outside/target.txt")
	if err != nil {
		t.Fatalf("Failed to create zip entry: %v", err)
	}
	_, err = io.WriteString(w, "malicious content")
	if err != nil {
		t.Fatalf("Failed to write zip content: %v", err)
	}
	zw.Close()
	
	// Change to extraction directory and extract
	oldCwd, _ := os.Getwd()
	defer os.Chdir(oldCwd)
	os.Chdir(extractDir)
	
	// Extract the zip
	zr, err := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
	if err != nil {
		t.Fatalf("Failed to create zip reader: %v", err)
	}
	
	err = ExtractZipArchive(zr)
	// Currently this does NOT error; the vulnerability allows extraction
	
	// Assert: the file outside the extraction directory was overwritten
	content, err := os.ReadFile(targetFile)
	if err != nil {
		t.Fatalf("Failed to read target file: %v", err)
	}
	
	if string(content) == "malicious content" {
		t.Errorf("VULNERABILITY: Path traversal allowed; file outside extraction root was overwritten")
	} else {
		t.Logf("File was not overwritten (expected after fix)")
	}
}
```

**Expected assertion after fix:** The test should either:
- Skip the traversal entry with a warning, or
- Return an error from `ExtractZipArchive`, or
- Ensure the file is written only within `extractDir` (not `../outside/target.txt`). [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L13-33)
```go
type extractor struct {
	r    io.ReaderAt
	size int64
	dir  string
}

// NewExtractor returns a new Zip Extractor.
func NewExtractor(r io.ReaderAt, size int64, dir string) (archive.Extractor, error) {
	return &extractor{r: r, size: size, dir: dir}, nil
}

// Extract extracts files from the reader to the directory passed to
// NewZipExtractor.
func (e *extractor) Extract(ctx context.Context) error {
	zr, err := zip.NewReader(e.r, e.size)
	if err != nil {
		return err
	}

	return archives.ExtractZipArchive(zr)
}
```

**File:** helpers/archives/zip_extract.go (L12-39)
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

**File:** commands/helpers/cache_extractor.go (L655-663)
```go
	extractor, err := archive.NewExtractor(format, f, size, wd)
	if err != nil {
		logrus.Fatalln(err)
	}

	err = extractor.Extract(context.Background())
	if err != nil {
		logrus.Fatalln(err)
	}
```
