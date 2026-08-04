### Title
Zip Slip path traversal in `ExtractZipArchive`/`extractZipFile` allows writing files outside job workspace - ([File: helpers/archives/zip_extract.go])

### Summary
`extractZipFile`, `extractZipFileEntry`, and `extractZipDirectoryEntry` use the raw, attacker-controlled `zip.File.Name` from a job artifact/cache archive directly in `os.MkdirAll`, `os.OpenFile`, `os.Symlink`, and `os.Remove` calls without validating that the resolved path stays within the extraction root. This is the classic "Zip Slip" pattern, allowing a crafted archive with `../../` segments (or an absolute path) in an entry name to write/overwrite files outside the intended job workspace.

### Finding Description
`ExtractZipArchive` iterates `archive.File` entries and, other than a single check for a leading `.git` path segment (`errorIfGitDirectory` in `helpers/archives/path_check_helper.go`), applies no path sanitization before calling `extractZipFile(file)`: [1](#0-0) 

`extractZipFile` builds the parent directory straight from `filepath.Dir(file.Name)` and passes it to `os.MkdirAll`, then dispatches to per-type handlers that call `os.OpenFile(file.Name, ...)` or `os.Symlink(..., file.Name)` — again using the raw, unsanitized name: [2](#0-1) 

`file.Name` comes directly from the zip archive being extracted (a job artifact or cache archive), which an unprivileged pipeline author fully controls (they can produce any zip bytes as a job artifact/cache upload). Go's `archive/zip` package itself does not sanitize `File.Name` against `../` segments or absolute paths (this responsibility is left to the caller — exactly the well-known Zip Slip class of bug). The only existing guard, `errorIfGitDirectory`, only special-cases names beginning with `.git` and does nothing to reject `..` traversal or absolute paths.

Note: this code path in `helpers/archives/zip_extract.go` (`ExtractZipFile`/`ExtractZipArchive`) appears to be a legacy zip-extraction implementation (referenced from `commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`), separate from the newer generic extractor used in `commands/helpers/cache_extractor.go` (`archive.NewExtractor(format, f, size, wd)`), which passes an explicit working-directory root and may contain its own path-confinement checks. I was not able to fully inspect `ziplegacy/zip_legacy_extractor.go` or the newer `archive` package's extractor implementation to confirm whether one of them wraps `ExtractZipArchive` with a path-confinement check before calling it, or whether callers pre-sanitize `zip.Reader.File[i].Name`. That gap should be verified before treating this purely as a legacy/dead code path.

### Impact Explanation
If reachable without an intervening path-confinement check (as the code in this file alone does not perform one), a crafted artifact/cache zip could write or overwrite arbitrary files reachable by the `gitlab-runner` process user (e.g. shell profile files, subsequent job scripts, or other files under directories the runner process can write to), which are then read/executed by later runner-generated shell scripts or by the user on next login — a workspace-confinement escape.

### Likelihood Explanation
Feasibility depends entirely on whether some caller of `ExtractZipArchive`/`ExtractZipFile` validates/rejects traversal entries before invoking it. Within the file under audit, no such validation exists, so if any code path calls `ExtractZipFile`/`ExtractZipArchive` on an attacker-supplied zip (cache or artifact) without pre-filtering entry names, the exploit is trivial and fully repeatable — a pipeline author only needs to construct a zip with a `zip.File.Name` like `../../../../home/gitlab-runner/.bashrc` and have it processed as a cache/artifact download.

### Recommendation
Add path-confinement validation for every `file.Name` before extraction: reject/skip entries where `filepath.Clean(file.Name)` is absolute, starts with `../`, or where the resolved joined path (`filepath.Join(destRoot, file.Name)`) does not have `destRoot` as a prefix (using `filepath.Rel` and checking for a resulting `..` prefix). Apply this check once in `ExtractZipArchive`/`extractZipFile` (or in a shared helper used by all archive extractors, similar to `errorIfGitDirectory`) so both the legacy zip extractor and any other callers are protected regardless of caller-provided root.

### Proof of Concept
```go
package archives

import (
	"archive/zip"
	"bytes"
	"os"
	"path/filepath"
	"testing"
)

func TestExtractZipArchive_PathTraversal(t *testing.T) {
	dir := t.TempDir()
	origWd, _ := os.Getwd()
	defer os.Chdir(origWd)
	os.Chdir(dir)

	// Build a malicious zip with a traversal entry name.
	buf := new(bytes.Buffer)
	zw := zip.NewWriter(buf)
	w, _ := zw.Create("../../../../tmp/zipslip_poc.txt")
	w.Write([]byte("pwned"))
	zw.Close()

	zr, err := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
	if err != nil {
		t.Fatal(err)
	}

	_ = ExtractZipArchive(zr)

	escapedPath := "/tmp/zipslip_poc.txt"
	if _, err := os.Stat(escapedPath); err != nil {
		t.Fatalf("expected traversal file to exist outside workspace, got err: %v", err)
	}
	// Assert the resolved path is not within the temp CWD.
	rel, err := filepath.Rel(dir, escapedPath)
	if err == nil && !bytes.HasPrefix([]byte(rel), []byte("..")) {
		t.Fatalf("expected escaped path outside dir, got rel=%s", rel)
	}
	os.Remove(escapedPath)
}
```
Expected assertion: the file is created outside the temp `dir` boundary, proving `ExtractZipArchive` does not confine extraction to the workspace root.

### Citations

**File:** helpers/archives/zip_extract.go (L41-66)
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

func extractZipFile(file *zip.File) (err error) {
	// Create all parents to extract the file
	err = os.MkdirAll(filepath.Dir(file.Name), 0o777)
	if err != nil {
		return err
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
