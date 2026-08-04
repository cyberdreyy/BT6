### Title
`ExtractZipArchive` has no path-containment check, letting attacker-controlled zip entries write/remove/symlink outside the restore root - ([File: helpers/archives/zip_extract.go])

### Summary
`ExtractZipArchive` (and its helpers `extractZipFile`, `extractZipDirectoryEntry`, `extractZipSymlinkEntry`, `extractZipFileEntry`) uses `file.Name` from the zip archive verbatim for `os.MkdirAll`, `os.Mkdir`, `os.Remove`, `os.OpenFile`, and `os.Symlink`, with no `filepath.Abs`/prefix check against the extraction root. This is unlike the sibling `tarzstd` extractor, which explicitly validates `!strings.HasPrefix(path, e.dir+string(filepath.Separator))` before touching the filesystem. An attacker who controls artifact or cache zip content (any pipeline author, since artifacts/caches are user-produced) can supply entries with `..` segments, absolute paths, or symlink entries that are later traversed by subsequent entries, causing writes, removals, or symlink creation outside the intended cache/artifact directory.

### Finding Description
`ExtractZipArchive` iterates `archive.File` and for each entry calls `extractZipFile`, which does: [1](#0-0) 
No path is ever normalized or checked against the extraction root (`wd`, passed into `archive.NewExtractor` in `commands/helpers/cache_extractor.go` and `commands/helpers/artifacts_downloader.go`) before being used in `os.MkdirAll(filepath.Dir(file.Name), ...)`, `os.Mkdir`, `os.Remove`, `os.OpenFile`, or `os.Symlink`.

Two attack primitives exist:
1. **Direct traversal**: an entry name like `../../etc/cron.d/x` or an absolute path is used unmodified by `os.MkdirAll`/`os.OpenFile`/`os.Remove`, letting the attacker create, overwrite, or delete files outside the restore root.
2. **Symlink-alias chaining**: `extractZipSymlinkEntry` removes any existing path and creates a symlink pointing to attacker-controlled data: [2](#0-1) 
A later entry named e.g. `alias/payload` will have its parent directory resolved with `os.MkdirAll(filepath.Dir(file.Name), 0o777)` at line 63 — Go's `os.MkdirAll`/`os.OpenFile` follow symlinks in intermediate path components, so if `alias` is a symlink to an external directory (e.g., `/tmp`, another job's workspace, or a shared cache root), subsequent entries silently escape the intended root and write/remove files there.

`ExtractZipArchive` does not abort on the first error; it logs (deduplicated via `pathErrorTracker`, see `helpers/archives/path_error_tracker.go`) and continues processing every remaining entry for the loop, and also continues into the `lchmod`/`processZipExtra` pass, regardless of any earlier failure. This means once an attacker triggers a failure (e.g. a deliberately malformed entry, permission conflict, or space exhaustion) mid-archive, the loop still processes all subsequent entries using the same unguarded path logic, including symlink aliases established earlier in the same archive — there's no root-recovery/abort/rollback step that would stop cross-directory effects.

Existing protections are insufficient: the only path-related check is `errorIfGitDirectory` (warns if a `.git` directory is touched) — it does not stop extraction or validate containment: [3](#0-2) 
There is no analog of the `tarzstd` extractor's containment check: [4](#0-3) 

This code is reachable from both cache and artifact extraction: `ExtractZipFile`/`ExtractZipArchive` is invoked by `ziplegacy.extractor.Extract`, which is selected by `archive.NewExtractor` for zip-format archives inside `CacheExtractorCommand.Execute` (`commands/helpers/cache_extractor.go`) and `ArtifactsDownloaderCommand.Execute` (`commands/helpers/artifacts_downloader.go`), both of which pass the job's working directory as the intended extraction root with no further path confinement enforced by the caller.

### Impact Explanation
An unprivileged pipeline author who controls the content of an artifact zip (their own job's artifacts, later consumed by a downstream job in the same pipeline) or a cache zip can craft entries that escape the restore root during `restore_cache`/`download_artifacts` stages. This can overwrite or delete files outside the intended cache/artifact directory on the runner's filesystem (e.g., other paths in the build directory, shared cache directories, or — in shell/executor configurations where multiple jobs share a host path — files belonging to other jobs), matching the "cross-job tampering through cleanup/path confusion" impact class. Because `os.MkdirAll`/`os.OpenFile` follow symlinks transparently, the effective blast radius is any path reachable via a symlink alias created earlier in the same archive, not just paths named with `..`.

### Likelihood Explanation
High feasibility: the attacker only needs to produce/name entries in a cache or artifact zip they control (via `.gitlab-ci.yml` cache/artifact configuration and job output), which is entirely within a normal, unprivileged pipeline author's control. No additional privilege, admin misconfiguration, or race condition is required — a single crafted zip triggers the behavior deterministically on every extraction. The `errorIfGitDirectory` check and `pathErrorTracker` only affect logging, not containment, so they don't block the exploit.

### Recommendation
Add an explicit path-containment check in `extractZipFile` (and before any filesystem mutation in `extractZipDirectoryEntry`, `extractZipSymlinkEntry`, `extractZipFileEntry`) mirroring the `tarzstd` extractor: resolve `filepath.Join(root, file.Name)`, compute `filepath.Abs`, and reject (abort the whole extraction, not just warn) any entry whose resolved path does not have `root+separator` as a prefix. Additionally, resolve symlink targets and re-validate containment for every subsequent entry whose parent directory chain includes a previously-created symlink (e.g., by using `filepath.EvalSymlinks` on the parent directory and re-checking the prefix, or by tracking created symlink paths and refusing to traverse through them). Consider making `ExtractZipArchive` abort on the first non-actionable path violation rather than continuing to process remaining entries.

### Proof of Concept
```go
func TestExtractZipArchive_PathTraversalEscapesRoot(t *testing.T) {
    dir := t.TempDir()
    root := filepath.Join(dir, "root")
    require.NoError(t, os.MkdirAll(root, 0o755))

    outside := filepath.Join(dir, "outside_marker")
    require.NoError(t, os.WriteFile(outside, []byte("original"), 0o644))

    // Build a zip with an entry name that escapes the root.
    var buf bytes.Buffer
    zw := zip.NewWriter(&buf)
    w, _ := zw.Create("../outside_marker")
    _, _ = w.Write([]byte("overwritten by attacker archive"))
    require.NoError(t, zw.Close())

    origWD, _ := os.Getwd()
    require.NoError(t, os.Chdir(root))
    defer os.Chdir(origWD)

    zr, err := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    require.NoError(t, err)

    err = archives.ExtractZipArchive(zr)
    require.NoError(t, err) // extraction "succeeds" despite escaping root

    content, _ := os.ReadFile(outside)
    // Assert the file outside root was NOT modified — this currently FAILS,
    // demonstrating the missing containment check.
    assert.Equal(t, "original", string(content))
}
```
A second PoC should build a symlink entry (`alias -> ../../outside_dir`) followed by an entry `alias/payload.txt`, and assert that `outside_dir/payload.txt` is never created.

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
