### Title
Zip-slip path traversal in legacy zip extractor bypasses cache extraction confinement to `wd` - ([File: commands/helpers/archive/ziplegacy/zip_legacy_extractor.go])

### Summary
`CacheExtractorCommand.Execute` calls `archive.NewExtractor(format, f, size, wd)` and then `Extract()` to unpack a cache archive that a prior job (or a poisoned shared cache blob under the same cache key) fully controls. When the resolved format uses the legacy zip backend, the `dir` (confinement root) argument is accepted by the constructor but never actually used during extraction, and the underlying entry-writing code performs no path sanitization at all, allowing crafted `..`/absolute-path zip entry names (and attacker-controlled symlink targets) to write files anywhere the runner process user can write, unlike the `tarzstd` extractor which explicitly validates this.

### Finding Description
`CacheExtractorCommand.Execute` (`commands/helpers/cache_extractor.go:618-664`) computes `wd, err := os.Getwd()` and passes it as the confinement directory to `archive.NewExtractor(format, f, size, wd)`, then calls `extractor.Extract(context.Background())`.

For the legacy zip backend, `ziplegacy.NewExtractor` stores `dir` in the `extractor` struct (`commands/helpers/archive/ziplegacy/zip_legacy_extractor.go:20-22`), but `Extract()` never references `e.dir`:

```go
func (e *extractor) Extract(ctx context.Context) error {
	zr, err := zip.NewReader(e.r, e.size)
	...
	return archives.ExtractZipArchive(zr)
}
``` [1](#0-0) 

`archives.ExtractZipArchive` iterates `archive.File` and calls `extractZipFile(file)`, which uses `file.Name` directly with no joining against, or validation against, any base directory:

```go
func extractZipFile(file *zip.File) (err error) {
	err = os.MkdirAll(filepath.Dir(file.Name), 0o777)
	...
	switch file.Mode() & os.ModeType {
	case os.ModeDir:
		err = extractZipDirectoryEntry(file)
	case os.ModeSymlink:
		err = extractZipSymlinkEntry(file)
	...
	default:
		err = extractZipFileEntry(file)
	}
}
``` [2](#0-1) 

`extractZipFileEntry` opens `os.OpenFile(file.Name, ...)` and `extractZipSymlinkEntry` calls `os.Symlink(string(data), file.Name)`, both using the raw, unsanitized `file.Name` from the zip header, with `data` (the symlink target) also being fully attacker-controlled. [3](#0-2) 

The only sanity check present, `errorIfGitDirectory`, is unrelated to path confinement — it only warns about `.git` paths, and its return value is treated as non-fatal (`tracker.actionable(err)` just logs a warning). [4](#0-3) 

This is in stark contrast to `tarzstd`'s extractor, which explicitly resolves each entry against `e.dir` and rejects anything that escapes it:

```go
path, err = filepath.Abs(filepath.Join(e.dir, hdr.Name))
...
if !strings.HasPrefix(path, e.dir+string(filepath.Separator)) && path != e.dir {
	return fmt.Errorf("%s cannot be extracted outside of chroot (%s)", path, e.dir)
}
``` [5](#0-4) 

Attacker path: a job controls the content of a cache archive it archives via `CacheArchiverCommand` (job files, names are attacker-controlled since the archiver iterates over paths the job specifies), or a job can poison a shared cache key/blob consumed later by another job sharing that key. When a subsequent job runs `CacheExtractorCommand` against that same/poisoned archive and the legacy zip backend is selected (the default/fallback zip implementation, exercised by the same `OnEachZipExtractor` test helper used for the fastzip backend), a zip entry name such as `../../../../home/gitlab-runner/.bashrc` or `/etc/cron.d/x` — or a symlink entry pointing outside `wd` followed by a same-named file entry — is extracted with no confinement check whatsoever, because `wd` (the intended root) is silently discarded by the legacy extractor.

### Impact Explanation
A job that controls cache archive content can, when a later job (its own subsequent job/stage, or another job sharing the same cache key on a shared runner/cache backend) extracts that cache using the legacy zip extractor, write or overwrite arbitrary files outside the job's working directory on the runner host filesystem, up to the privileges of the runner/job process. This directly breaches the "file operations must stay within intended build/cache/artifact roots" invariant and enables cross-job or host file overwrite via cache poisoning, matching the scoped impact.

### Likelihood Explanation
The precondition is only that (a) an attacker can create a malicious cache archive (fully achievable by any job author via `CacheArchiverCommand`, since names of archived files/paths originate from job-controlled data) and (b) that archive is later extracted by `CacheExtractorCommand` using the legacy zip backend (a normal, still-active code path — exercised in `cache_extractor_test.go`'s `OnEachZipExtractor` test harness alongside fastzip). No special privileges, admin misconfiguration, or leaked credentials are required. This is fully repeatable and deterministic given a crafted zip.

### Recommendation
In `commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`, make `Extract()` actually use `e.dir` and validate every entry path against it before writing, mirroring `tarzstd`'s check (`filepath.Abs(filepath.Join(e.dir, name))` + `strings.HasPrefix` confinement check), and reject/skip entries with absolute paths or resolved paths outside the root, and reject symlink entries whose target would resolve outside the root before creating them. Since `archives.ExtractZipArchive`/`extractZipFile` are shared with other zip consumers, add the confinement check either by passing the target `dir` into `ExtractZipArchive`/`extractZipFile` and joining/validating there, or perform validation in `ziplegacy.Extract()` before delegating.

### Proof of Concept
Add to `commands/helpers/archive/ziplegacy` (or `helpers/archives`) a test:
```go
func TestZipLegacyExtractor_PathTraversal(t *testing.T) {
    tmpDir := t.TempDir()
    outsideDir := t.TempDir()
    targetOutside := filepath.Join(outsideDir, "pwned.txt")

    var buf bytes.Buffer
    zw := zip.NewWriter(&buf)
    // relative traversal escaping tmpDir into outsideDir
    rel, _ := filepath.Rel(tmpDir, targetOutside)
    w, _ := zw.Create(rel)
    w.Write([]byte("owned"))
    zw.Close()

    r := bytes.NewReader(buf.Bytes())
    ext, err := ziplegacy.NewExtractor(r, int64(buf.Len()), tmpDir)
    require.NoError(t, err)

    err = ext.Extract(context.Background())
    require.NoError(t, err) // extraction succeeds

    _, statErr := os.Stat(targetOutside)
    assert.NoError(t, statErr, "file was written outside confinement dir - zip slip") // FAILS today: file gets created outside tmpDir
}
```
Expected result on current code: `targetOutside` file exists (bug confirmed). Expected after fix: `Extract` returns an error like `"<path> cannot be extracted outside of chroot (<dir>)"` and no file is created outside `tmpDir`, matching `tarzstd`'s guarded behavior.

### Citations

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L26-33)
```go
func (e *extractor) Extract(ctx context.Context) error {
	zr, err := zip.NewReader(e.r, e.size)
	if err != nil {
		return err
	}

	return archives.ExtractZipArchive(zr)
}
```

**File:** helpers/archives/zip_extract.go (L22-59)
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
