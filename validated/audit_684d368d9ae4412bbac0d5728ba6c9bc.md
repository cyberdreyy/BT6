### Title
Zip-slip path traversal in `extractZipFile`/`extractZipFileEntry` via unsanitized `zip.File.Name` - ([File: helpers/archives/zip_extract.go])

### Summary
`ExtractZipArchive` iterates `archive.File` and passes each entry's raw `file.Name` directly to `extractZipFile`, which calls `os.MkdirAll(filepath.Dir(file.Name), ...)` and, via `extractZipFileEntry`/`extractZipSymlinkEntry`, `os.OpenFile`/`os.Symlink` on `file.Name` without any `filepath.Clean`, absolute-path rejection, or containment check against the extraction root. This is the classic zip-slip pattern, and the codebase demonstrably knows how to guard against it elsewhere (see the `tarzstd` extractor's explicit prefix check) but does not apply an equivalent guard here.

### Finding Description
`ExtractZipArchive` (helpers/archives/zip_extract.go:85-96) loops over `archive.File` and calls `extractZipFile(file)` for every entry with no validation of `file.Name`. `extractZipFile` (lines 61-83) does:
```go
err = os.MkdirAll(filepath.Dir(file.Name), 0o777)
...
default:
    err = extractZipFileEntry(file)
```
and `extractZipFileEntry` (lines 41-59) does:
```go
_ = os.Remove(file.Name)
out, err = os.OpenFile(file.Name, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, file.Mode().Perm())
```
`extractZipSymlinkEntry` similarly calls `os.Symlink(string(data), file.Name)` on the raw name. None of these paths are joined against, or validated to remain within, a fixed extraction root — the code operates on `file.Name` as a bare relative/absolute filesystem path, relative to the process's current working directory at call time.

By contrast, the `tarzstd` extractor in the same repo (commands/helpers/archive/tarzstd/tarzstd_extractor.go:57-64) explicitly computes `filepath.Abs(filepath.Join(e.dir, hdr.Name))` and rejects any path that doesn't have `e.dir` as a prefix — proving the project is aware of this class of bug and mitigates it for tar/zstd but not for zip.

The only entry-name check present, `errorIfGitDirectory`, only warns about `.git` directory entries (helpers/archives/zip_extract.go:89-91); it does not reject or sanitize `..` segments or absolute paths, and even on a warning `extractZipFile` is still invoked immediately after.

Attacker input: a job author or artifact/cache producer fully controls the `zip.File.Name` values inside an artifact or cache zip that is later extracted by `ExtractZipFile`/`ExtractZipArchive` during job artifact download or cache restore. A crafted entry named `../../etc/passwd` or `../sibling-project/secret` would cause:
- `filepath.Dir("../../etc/passwd")` → `../../etc`, then `os.MkdirAll` creates that directory relative to CWD (the job's working directory), potentially escaping it.
- `os.OpenFile`/`os.Remove` then write/overwrite the file outside the intended build directory.
- A malicious symlink entry can also be planted with a `../`-escaping name to create a symlink outside the working directory pointing anywhere, which subsequent file entries can then write through.

### Impact Explanation
This allows an unprivileged pipeline author supplying a crafted cache/artifact zip to write or overwrite files outside the job's working directory on the host or executor filesystem region the runner process can access (e.g., other job/project directories on a shared shell/host executor, or files within the container filesystem for docker/kubernetes executors). This matches the scoped impact: unauthorized file access/overwrite outside the job workspace via job-controlled archive contents.

### Likelihood Explanation
Preconditions are realistic and fully attacker-controlled: any job can produce artifacts, and any project can populate a cache; these zips are consumed by the runner (via `ExtractZipFile`/`ExtractZipArchive`) without size/path validation. No admin action or privileged component is needed — a pipeline author only needs to construct a zip with `..`-containing entry names (trivial with any zip library, since `archive/zip`'s writer does not prevent writing such names) and have the runner download/extract it as a cache or artifact for a subsequent job. This is reliably reproducible with a unit test.

### Recommendation
In `extractZipFile` (and analogous entry functions), resolve each entry against a fixed extraction root using `filepath.Join(root, file.Name)`, then verify with `filepath.Abs` + `strings.HasPrefix` (or `filepath.Rel` + check for a leading `..`) that the resulting path stays within `root`, rejecting/erroring out otherwise — mirroring the containment check already implemented in `commands/helpers/archive/tarzstd/tarzstd_extractor.go:58-64`. Apply the same check before creating directories, files, and symlinks (including validating symlink targets, since `extractZipSymlinkEntry` writes attacker-controlled link data as well).

### Proof of Concept
```go
func TestExtractZipArchive_PathTraversalRejected(t *testing.T) {
    tmpRoot := t.TempDir()
    // build in-memory zip with a traversal entry
    buf := new(bytes.Buffer)
    zw := zip.NewWriter(buf)
    w, _ := zw.Create("../../etc/pwned")
    _, _ = w.Write([]byte("owned"))
    require.NoError(t, zw.Close())

    zr, err := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    require.NoError(t, err)

    err = os.Chdir(tmpRoot) // simulate job working dir
    require.NoError(t, err)

    err = archives.ExtractZipArchive(zr)

    // Expected: extraction must fail or the file must not exist outside tmpRoot
    _, statErr := os.Stat(filepath.Join(tmpRoot, "..", "..", "etc", "pwned"))
    assert.True(t, os.IsNotExist(statErr), "traversal file should not be created outside extraction root")
}
```
This test currently fails against `ExtractZipArchive` because no containment check exists, confirming the vulnerability. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** helpers/archives/zip_extract.go (L41-83)
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
