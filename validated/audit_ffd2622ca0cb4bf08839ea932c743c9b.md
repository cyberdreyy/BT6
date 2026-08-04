This confirms the key finding: the legacy zip extractor is invoked via `CacheExtractorCommand.Execute` and `ArtifactsDownloaderCommand.Execute` with `wd` (the job's build/working directory) passed as `dir`, but `ziplegacy`'s `extractor.Extract` never uses `e.dir` at all — it just calls `archives.ExtractZipArchive(zr)` which operates purely on `file.Name` (the attacker-controlled zip entry name) with no join-and-contain step, relying instead on the test harness/production code doing an implicit `os.Chdir` to the target directory beforehand (as visible in the test `commands/helpers/archiver_test.go:69` "hack: legacy archiver require being in the correct working dir"). Meanwhile `tarzstd_extractor.go` explicitly joins `hdr.Name` against `e.dir` and enforces `strings.HasPrefix(path, e.dir+string(filepath.Separator))`. This is a genuine divergence in containment logic between the two extractor implementations, both reachable from job-controlled cache/artifact zip content downloaded and extracted by `gitlab-runner cache-extractor`/`artifacts-downloader`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

### Title
Legacy zip extractor performs no path containment check on zip entry names (Zip Slip) - ([File: helpers/archives/zip_extract.go])

### Summary
`archives.ExtractZipArchive` / `extractZipFile` (helpers/archives/zip_extract.go) writes each zip entry using the raw `zip.File.Name` (and symlink target data) directly to `os.Mkdir`, `os.OpenFile`, `os.Symlink`, and `os.Remove`, with no `filepath.Clean`/`filepath.Abs`/prefix check against the extraction root. This is the default extractor for the `Zip`/`ZipZstd` formats (`ziplegacy.NewExtractor`) used by `gitlab-runner cache-extractor` and `gitlab-runner artifacts-downloader`, both of which extract job/pipeline-controlled cache and artifact archives.

### Finding Description
`ziplegacy.extractor.Extract` (commands/helpers/archive/ziplegacy/zip_legacy_extractor.go:26-32) receives a `dir` at construction time from the caller (`wd`, the job's working directory, per `cache_extractor.go:655` and `artifacts_downloader.go:131`), but the field is never referenced in `Extract()` — it calls `archives.ExtractZipArchive(zr)` with no destination argument at all. `extractZipFile` (helpers/archives/zip_extract.go:41-83) then operates purely on `file.Name`: `os.MkdirAll(filepath.Dir(file.Name), ...)`, `os.Mkdir(file.Name, ...)`, `os.OpenFile(file.Name, ...)`, and for symlinks, `os.Symlink(string(data), file.Name)` where `data` is the fully attacker-controlled symlink target read from the archive entry's content. None of these paths are joined against, cleaned relative to, or validated as contained within any extraction root.

By contrast, `tarzstd.extractor.Extract` (commands/helpers/archive/tarzstd/tarzstd_extractor.go:57-64) explicitly computes `path = filepath.Abs(filepath.Join(e.dir, hdr.Name))` and rejects the entry unless `strings.HasPrefix(path, e.dir+string(filepath.Separator))`. This is exactly the divergence the question asks about: one extractor enforces containment, the other has none at all — and the one with none is the default handler for the `Zip`/`ZipZstd` cache/artifact format (fastzip is only used when `FF_USE_FASTZIP` is enabled, and even fastzip's containment guarantee depends entirely on the external `saracen/fastzip` library, not on any Runner-side check).

The only reason the legacy path appears to work correctly in the test suite is that the caller (or process) is chdir'd into the destination directory before extraction, as documented in the test comment "hack: legacy archiver require being in the correct working dir" (commands/helpers/archiver_test.go:68-69). Nothing in `CacheExtractorCommand.Execute` or `ArtifactsDownloaderCommand.Execute` performs an `os.Chdir` — they pass `wd` as the "dir" argument to `archive.NewExtractor`, which the legacy zip extractor silently discards. Extraction therefore proceeds relative to whatever the process's actual current working directory happens to be, with zero enforcement that entry names stay within it.

An attacker who controls a job (or a job pipeline whose artifacts/cache a later job or shell-executor job trusts) can craft a cache or artifact zip with an entry named e.g. `../../../../home/user/.ssh/authorized_keys`, or a symlink entry pointing outside the build directory followed by a regular-file entry through that symlink, and have it written wherever the traversal resolves relative to the extraction working directory when a subsequent job/stage runs `cache-extractor`/`artifacts-downloader` against that same runner host (relevant for shell/SSH executors and any executor where the runner's own filesystem is shared across job invocations on that host).

### Impact Explanation
On the shell/custom/SSH executor (or any executor mode where cache/artifact extraction happens on a filesystem that persists or is shared beyond the sandboxed container), a crafted cache/artifact zip lets a job write arbitrary files outside its build directory — e.g., overwriting files in the runner user's home directory, other projects' build directories, or runner configuration — purely via path traversal in zip entry names, since no containment check exists at all in this code path. This breaks the "file operations must stay within intended build/cache/artifact roots" invariant.

### Likelihood Explanation
Preconditions: the attacker needs the ability to author a project's `.gitlab-ci.yml`/cache or artifact contents (any normal pipeline author) and a runner where `cache-extractor`/`artifacts-downloader` extracts using the legacy zip format (default unless `FF_USE_FASTZIP` is enabled) on a host/executor where the extraction target isn't strictly sandboxed per job. This is fully attacker-controlled and repeatable — the attacker directly authors the archive content that becomes the extraction source.

### Recommendation
Make `ziplegacy.extractor.Extract` actually use `e.dir`: join every `file.Name` against `e.dir`, `filepath.Clean`/`filepath.Abs` the result, and reject (or skip with a warning) any entry whose resolved path escapes `e.dir`, mirroring the check already implemented in `tarzstd_extractor.go`. Apply the same containment check to symlink targets (`extractZipSymlinkEntry`), not just directory/file entry names, since a symlink can be a two-stage escape vector.

### Proof of Concept
Go unit test differential PoC (add near `commands/helpers/archiver_test.go` or a new test in `helpers/archives`):
```go
func TestZipSlip_LegacyExtractor(t *testing.T) {
    // Build a malicious zip with an entry name "../evil.txt"
    buf := new(bytes.Buffer)
    zw := zip.NewWriter(buf)
    f, _ := zw.Create("../evil.txt")
    f.Write([]byte("pwned"))
    zw.Close()

    outerDir := t.TempDir()
    targetDir := filepath.Join(outerDir, "target")
    os.Mkdir(targetDir, 0777)

    origWd, _ := os.Getwd()
    defer os.Chdir(origWd)
    os.Chdir(targetDir)

    zr, _ := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    err := archives.ExtractZipArchive(zr)

    // Assert containment: file must NOT exist outside targetDir
    _, statErr := os.Stat(filepath.Join(outerDir, "evil.txt"))
    assert.Error(t, statErr, "zip slip: file escaped extraction root; legacy extractor allowed traversal, err=%v", err)
}

func TestZipSlip_TarZstdExtractor_Rejects(t *testing.T) {
    // same "../evil.txt" entry via tar+zstd, using tarzstd.NewExtractor(r, size, dir)
    // assert extractor.Extract returns an error containing "cannot be extracted outside of chroot"
}
```
Expected result today: `TestZipSlip_LegacyExtractor` fails the assertion (file is written outside `targetDir`, confirming no containment), while `TestZipSlip_TarZstdExtractor_Rejects` passes (tarzstd rejects the same payload) — proving the divergence described in the question.

### Citations

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

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L26-32)
```go
func (e *extractor) Extract(ctx context.Context) error {
	zr, err := zip.NewReader(e.r, e.size)
	if err != nil {
		return err
	}

	return archives.ExtractZipArchive(zr)
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

**File:** commands/helpers/cache_extractor.go (L654-660)
```go

	extractor, err := archive.NewExtractor(format, f, size, wd)
	if err != nil {
		logrus.Fatalln(err)
	}

	err = extractor.Extract(context.Background())
```

**File:** commands/helpers/artifacts_downloader.go (L125-140)
```go
	f, size, format, err := openArchive(file.Name())
	if err != nil {
		logrus.Fatalln(err)
	}
	defer f.Close()

	extractor, err := archive.NewExtractor(format, f, size, wd)
	if err != nil {
		logrus.Fatalln(err)
	}

	// Extract artifacts file
	err = extractor.Extract(context.Background())
	if err != nil {
		logrus.Fatalln(err)
	}
```
