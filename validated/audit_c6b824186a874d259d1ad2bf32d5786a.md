### Title
Zip extraction path/symlink traversal ("zip-slip") in `ExtractZipArchive` due to missing path confinement — ([File: helpers/archives/zip_extract.go])

### Summary
`ExtractZipArchive` and its helpers (`extractZipFile`, `extractZipSymlinkEntry`, `extractZipFileEntry`) use each `zip.File.Name` verbatim with `os.OpenFile`/`os.Symlink`/`os.MkdirAll`, performing no path-confinement check comparable to `tarzstd_extractor.go`'s `filepath.Abs` + `HasPrefix` chroot check, and `errorIfGitDirectory` in `path_check_helper.go` only blocks `.git`-prefixed paths. This allows a crafted archive to write files outside the extraction root via a symlink-as-directory technique (and even more directly via a plain `..`-containing entry name), confirming the reported class of vulnerability, though the exact "two entries with the identical name" mechanic in the question does not work as literally stated.

### Finding Description
`extractZipFile` (helpers/archives/zip_extract.go:61-83) dispatches on `file.Mode()&os.ModeType` and, for every entry, first calls `os.MkdirAll(filepath.Dir(file.Name), 0o777)` (line 63) before creating the entry. For symlink entries, `extractZipSymlinkEntry` (lines 22-39) writes the file's own content as the symlink target via `os.Symlink(string(data), file.Name)` with **no validation of the target** (absolute paths, `..`, or anything else). For regular files, `extractZipFileEntry` (lines 41-59) does `os.Remove(file.Name)` then `os.OpenFile(...)` + `io.Copy`, again with `file.Name` used unmodified.

`ExtractZipArchive` (lines 85-110) only runs `errorIfGitDirectory` (helpers/archives/path_check_helper.go:21-31), which merely checks whether the top path component is literally `.git` — it performs no `filepath.Clean`/`filepath.Abs`/`HasPrefix` containment check against the destination root, unlike `commands/helpers/archive/tarzstd/tarzstd_extractor.go:57-64`, which explicitly computes `filepath.Abs(filepath.Join(e.dir, hdr.Name))` and rejects any path that escapes `e.dir`.

The exact "same name, symlink then file" sequence in the question does **not** work as literally exploitable, because `extractZipFileEntry` calls `os.Remove(file.Name)` before `os.OpenFile`, which removes the symlink node itself (not the target) before the second entry is written — so the second write lands as a fresh regular file at the same path, not through the old symlink.

However, the underlying flaw (no path/symlink confinement) is real and exploitable via a closely related, still fully attacker-controlled technique:
1. Entry 1: symlink named e.g. `linkdir` with target `../../other-project-workspace` (or any relative escape).
2. Entry 2: regular file named `linkdir/secrets.env`.
   `extractZipFile`'s `os.MkdirAll(filepath.Dir("linkdir/secrets.env"))` = `os.MkdirAll("linkdir")`; since `linkdir` already exists (as a symlink resolving to an existing directory), `MkdirAll` sees it as an existing directory and returns `nil` without error. `extractZipFileEntry` then opens `"linkdir/secrets.env"`, which the OS resolves through the symlink, causing `os.OpenFile`/`io.Copy` to write outside the intended extraction root.

Additionally, because there is no `..`-traversal check on `file.Name` at all, a single entry literally named `../../other-project-workspace/secrets.env` would write outside the root directly, with no symlink needed — an even simpler variant of the same missing-confinement bug.

### Impact Explanation
An attacker-controlled cache/artifact zip extracted via `ExtractZipArchive` (reached from `commands/helpers/archive/ziplegacy/zip_legacy_extractor.go:26-32` and `ExtractZipFile`) can write or overwrite files outside the intended job/cache/artifact directory. On executors where extraction directories are shared or predictable across jobs/projects (e.g., certain Docker/Kubernetes volume configurations), this can lead to cross-project file overwrite or exfiltration of data outside the job root, matching the scoped impact. [1](#0-0) [2](#0-1) 

### Likelihood Explanation
This is fully reachable by an unprivileged pipeline author: cache/artifact zip contents are entirely attacker-controlled and require no special privileges to upload. The zip-slip-via-symlink technique is well-understood and reliably repeatable; only the exact "same name" variant from the question fails due to `os.Remove` preemptively deleting the symlink. Practical impact depends on the executor sharing extraction directories or using predictable paths across jobs (Docker/Kubernetes with shared volumes), which is a plausible but non-default configuration.

### Recommendation
Add explicit path-confinement validation in `ExtractZipArchive`/`extractZipFile`, mirroring `tarzstd_extractor.go`: compute `filepath.Abs(filepath.Join(destRoot, file.Name))` and reject entries whose resolved path escapes `destRoot` (via `HasPrefix` check on the cleaned absolute path). Additionally, validate symlink targets (`extractZipSymlinkEntry`) so that a symlink cannot resolve outside `destRoot`, and validate that no path component of `file.Name` is itself a pre-existing symlink pointing outside the destination before calling `os.MkdirAll`/`os.OpenFile` on it (e.g., using `os.Lstat` per path segment or `os.OpenFile` with `O_NOFOLLOW`-equivalent semantics).

### Proof of Concept
```go
func TestExtractZipArchive_SymlinkDirectoryEscape(t *testing.T) {
    outsideDir := t.TempDir()
    destDir := t.TempDir()

    var buf bytes.Buffer
    zw := zip.NewWriter(&buf)

    // Entry 1: symlink "linkdir" -> outsideDir (relative escape)
    relTarget, _ := filepath.Rel(destDir, outsideDir)
    linkHdr := &zip.FileHeader{Name: "linkdir"}
    linkHdr.SetMode(os.ModeSymlink | 0777)
    w, _ := zw.CreateHeader(linkHdr)
    w.Write([]byte(relTarget))

    // Entry 2: file "linkdir/secrets.env" -> written through the symlink
    fileHdr := &zip.FileHeader{Name: "linkdir/secrets.env"}
    fileHdr.SetMode(0644)
    w2, _ := zw.CreateHeader(fileHdr)
    w2.Write([]byte("stolen"))

    zw.Close()

    oldwd, _ := os.Getwd()
    os.Chdir(destDir)
    defer os.Chdir(oldwd)

    zr, _ := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    err := archives.ExtractZipArchive(zr)
    require.NoError(t, err)

    // Assert the file escaped destDir and landed in outsideDir
    escaped := filepath.Join(outsideDir, "secrets.env")
    _, statErr := os.Stat(escaped)
    assert.NoError(t, statErr, "file should have escaped into outsideDir, proving zip-slip")
}
```
Expected result on the current code: the assertion passes (file found in `outsideDir`), proving the extraction escaped the destination root — confirming the missing confinement check.

### Citations

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
