### Title
`ExtractZipFile`/`ExtractZipArchive` extract symlink entries with no path or target validation, allowing symlink-pivot writes outside the restore root - (File: helpers/archives/zip_extract.go)

### Summary
`extractZipFile` dispatches on the zip entry's mode and, for `os.ModeSymlink` entries, calls `extractZipSymlinkEntry`, which creates a symlink at `file.Name` pointing to an attacker-controlled target read directly from the zip entry's content, with no validation of the target or of `file.Name` itself. Because no code path (`extractZipFile`, `extractZipDirectoryEntry`, `extractZipFileEntry`) checks that resolved paths stay inside the restore root, an attacker-crafted archive can create a symlinked directory alias pointing outside the root and then extract subsequent "files" through that alias to write into a trusted external location.

### Finding Description
In `helpers/archives/zip_extract.go`: [1](#0-0) 

`extractZipSymlinkEntry` reads the symlink target directly from the entry's file content (fully attacker-controlled — can be absolute, or contain `..`) and calls `os.Symlink(string(data), file.Name)` with no sanitization. Likewise, `extractZipFile`'s parent-directory creation: [2](#0-1) 

calls `os.MkdirAll(filepath.Dir(file.Name), 0o777)` before dispatching to directory/symlink/file handlers, and `extractZipFileEntry`/`extractZipDirectoryEntry` operate directly on `file.Name` without any `filepath.Clean` + root-prefix check. `ExtractZipArchive` iterates all entries and calls `extractZipFile` for each with only a `.git`-directory guard (`errorIfGitDirectory`) — nothing related to path escape or symlink targets: [3](#0-2) 

This is a clear, missing check when compared to the sibling `tarzstd` extractor in the same codebase, which explicitly resolves and validates every entry path before writing: [4](#0-3) 

That extractor computes `filepath.Abs(filepath.Join(e.dir, hdr.Name))` and rejects any path that does not have `e.dir` as a prefix — a check entirely absent from `zip_extract.go`.

Exploit flow:
1. Archive entry `linkdir` is a symlink (`os.ModeSymlink`) whose "content" is an absolute path or a `..`-relative path pointing to a directory outside the restore root (e.g., a shared cache directory, another job's workspace, or any writable location reachable by the Runner process).
2. `extractZipSymlinkEntry` creates this symlink unconditionally via `os.Symlink(target, "linkdir")`.
3. A subsequent regular-file entry named `linkdir/payload` is processed by `extractZipFile`, which calls `os.MkdirAll(filepath.Dir("linkdir/payload"))` — this call follows the existing symlink `linkdir` (since `MkdirAll`/`os.Stat` follow symlinks and see it already "exists" as a directory) and then `extractZipFileEntry` opens/writes `linkdir/payload`, which the OS resolves through the symlink to the external target directory.
4. The net effect is a file write into an external, trusted directory outside the intended restore root, using only the extraction root path plus an attacker-supplied archive.

No overwrite guard, path-prefix check, or symlink-target validation exists anywhere in this file to stop step 3, unlike the tar/zstd path.

### Impact Explanation
An attacker who controls cache or artifact archive content for a job (e.g., via a poisoned cache key collision, artifact re-upload, or any job stage that produces/consumes a zip archive later extracted by another job/pipeline) can use this symlink pivot to write or overwrite files in a directory outside the intended extraction root that the Runner process can reach — e.g., a shared cache directory used by other jobs/projects, leading to cross-job tampering (planting malicious files consumed by a later job) or overwriting artifacts belonging to other jobs. The concrete blast radius depends on Runner's directory layout and OS-level permissions of the process performing extraction, but the code path itself imposes no in-code restriction.

### Likelihood Explanation
The precondition is simply "a job can produce/consume a zip archive extracted through `ExtractZipFile`/`ExtractZipArchive`" (this legacy zip path is still wired up, e.g. referenced from `commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`), and the attacker only needs control over that archive's bytes/entry structure — squarely within "attacker controls archive bytes, entry names, links, and metadata" per the threat model. No special privileges are required beyond being able to produce or influence artifact/cache content for a job, which is a normal, unprivileged pipeline-author capability. The bug is fully deterministic and repeatable — it doesn't depend on race conditions.

### Recommendation
In `helpers/archives/zip_extract.go`, before performing any filesystem operation for an entry:
1. Resolve `file.Name` against the extraction root with `filepath.Join` + `filepath.Abs`/`filepath.Clean`, and reject entries whose resolved path is not a descendant of the root (mirroring the check already used in `commands/helpers/archive/tarzstd/tarzstd_extractor.go`).
2. For symlink entries, validate the resolved symlink target the same way — reject any target (absolute or via `..`) that would resolve outside the root — and reject creating a symlink whose name resolves outside the root.
3. Additionally, when creating parent directories via `os.MkdirAll(filepath.Dir(file.Name), ...)`, verify no path component is an existing symlink pointing outside the root (e.g., using `os.Lstat` per path segment or extracting into a directory opened with `O_NOFOLLOW` semantics via `os.Root`/`openat`-style APIs where available).

### Proof of Concept
Go unit test in `helpers/archives/zip_extract_test.go` style:

```go
func TestExtractZipFile_SymlinkPivotEscapesRoot(t *testing.T) {
    outsideDir := t.TempDir()          // simulates trusted external location
    root := t.TempDir()                // restore root
    zipPath := filepath.Join(root, "evil.zip")

    // Build a malicious zip:
    // 1) symlink entry "linkdir" -> outsideDir (absolute path)
    // 2) regular file entry "linkdir/payload.txt" with attacker content
    f, _ := os.Create(zipPath)
    zw := zip.NewWriter(f)

    symHdr := &zip.FileHeader{Name: "linkdir"}
    symHdr.SetMode(os.ModeSymlink | 0777)
    w, _ := zw.CreateHeader(symHdr)
    w.Write([]byte(outsideDir))

    fileHdr := &zip.FileHeader{Name: "linkdir/payload.txt"}
    fileHdr.SetMode(0644)
    w2, _ := zw.CreateHeader(fileHdr)
    w2.Write([]byte("PWNED"))

    zw.Close()
    f.Close()

    wd, _ := os.Getwd()
    os.Chdir(root)
    defer os.Chdir(wd)

    err := archives.ExtractZipFile(zipPath)
    require.NoError(t, err)

    // Assertion: payload.txt must NOT appear inside outsideDir (root escape)
    _, statErr := os.Stat(filepath.Join(outsideDir, "payload.txt"))
    assert.True(t, os.IsNotExist(statErr),
        "symlink pivot succeeded: file was written outside restore root into %s", outsideDir)
}
```

Expected current behavior (bug present): `payload.txt` is found inside `outsideDir`, proving the escape. After applying the recommended path/symlink validation, `ExtractZipFile` should return an error and no file should be created outside `root`.

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

**File:** helpers/archives/zip_extract.go (L85-110)
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

	for _, file := range archive.File {
		if err := lchmod(file.Name, file.Mode()); tracker.actionable(err) {
			logrus.Warningf("%s: %s (suppressing repeats)", file.Name, err)
		}

		// Process zip metadata
		if err := processZipExtra(&file.FileHeader); tracker.actionable(err) {
			logrus.Warningf("%s: %s (suppressing repeats)", file.Name, err)
		}
	}

	return nil
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
