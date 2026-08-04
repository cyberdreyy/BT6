### Title
Zip-slip path traversal in the legacy zip extractor used by artifact download - allows overwrite of sibling job/cache directories ([File: helpers/archives/zip_extract.go], [File: commands/helpers/archive/ziplegacy/zip_legacy_extractor.go])

### Summary
`shells/abstract.go:downloadArtifacts` shells out to the `artifacts-downloader` command, which downloads a dependency job's artifact archive and extracts it via `archive.NewExtractor(format, f, size, wd)` [1](#0-0) . When the archive format resolves to `zip` and the legacy zip extractor is registered, `ziplegacy.extractor.Extract` completely discards the `dir` (chroot) parameter and extracts entries using their raw, attacker-controlled `zip.File.Name` with no path-containment check, unlike the `tarzstd` and `fastzip` extractors which do enforce a chroot boundary.

### Finding Description
- `downloadArtifacts` in `shells/abstract.go` invokes `artifacts-downloader --id <dependency job id> --token <job token>` [2](#0-1) , and the token/id refer to an *earlier dependency job* whose artifact contents are attacker-controlled by whoever produced that job's artifacts.
- `ArtifactsDownloaderCommand.Execute` downloads the archive, detects the format from magic bytes, and calls `archive.NewExtractor(format, f, size, wd)` where `wd = os.Getwd()` (the current job's working directory) [1](#0-0) .
- The `archive.Extractor` interface's `dir` parameter is intended to scope/chroot extraction to that working directory, and this is enforced correctly by the `tarzstd` extractor, which computes `path := filepath.Join(e.dir, hdr.Name)` and rejects any entry whose resolved path escapes `e.dir` [3](#0-2) .
- However, `ziplegacy.extractor.Extract` never uses `e.dir` at all — it only opens the zip reader and calls `archives.ExtractZipArchive(zr)`, passing no directory/chroot argument whatsoever [4](#0-3) .
- Inside `archives.ExtractZipArchive` → `extractZipFile`, each entry is written directly to `file.Name` — the raw path from the zip header — via `os.MkdirAll(filepath.Dir(file.Name), ...)` and `os.OpenFile(file.Name, ...)`/`os.Symlink(...)`, with **no** validation that `file.Name` stays within any directory boundary and no rejection of `..` segments or absolute paths [5](#0-4) .
- Because paths are resolved relative to the process's current working directory (not clamped to it), a malicious dependency-job artifact zip containing an entry named e.g. `../other-job-slug/cache/malicious` or `../../builds/<other-project>/exploit` would be written outside the current job's root, potentially into a sibling build or cache directory on the same runner host/executor filesystem.
- None of the "existing checks" mentioned in the question (overwrite guards, path validation) apply to this legacy extractor path — the chroot check exists only in `tarzstd_extractor.go`, and `fastzip`'s protection is delegated to the third-party `saracen/fastzip` library, not to any in-repo check.

### Impact Explanation
On shell/shared-host executors where multiple jobs' build/cache directories reside as siblings under a common runner data directory, an attacker who controls (or can influence) the contents of an earlier dependency job's artifact zip can craft entry names with `../` traversal to write files into another job's build or cache directory when that legacy zip extractor path is used for artifact extraction between jobs. This constitutes cross-job state tampering (e.g., planting malicious files that a sibling job may later execute or trust), matching the "cross-project or cross-job state tampering" impact scoped in the question.

### Likelihood Explanation
Preconditions: (1) the runner uses the legacy zip extractor path (`ziplegacy`) for zip-format artifacts — this is a real, reachable code path (`archive.Register`d and dispatched via `archive.NewExtractor`); (2) the attacker must have been able to produce/modify a dependency job's artifact archive, which is achievable by any user who can run an earlier job in the same pipeline whose artifacts are declared as a dependency of a later job — a normal, unprivileged pipeline-author capability. The exploit is fully attacker-controlled (archive bytes/entry names) and deterministic/repeatable — no timing races or admin/service compromise are needed.

### Recommendation
Add the same containment check used in `tarzstd_extractor.go` to the legacy zip extraction path: resolve each `file.Name` against the intended `dir`, use `filepath.Clean`/`filepath.Abs`, and reject any entry whose resolved path is not a strict descendant of `dir` (and reject absolute paths and `..` segments) before performing `MkdirAll`/`OpenFile`/`Symlink` in `helpers/archives/zip_extract.go`. Additionally, wire the `dir` parameter into `ziplegacy.extractor.Extract` (currently discarded) so extraction is actually confined to the caller-provided directory rather than the process's ambient working directory.

### Proof of Concept
Go unit test plan (to add under `helpers/archives/zip_extract_test.go` or `commands/helpers/archive/ziplegacy`):
1. Create a temp directory `jobDir` to represent the current job's working directory, and a sibling temp directory `siblingDir` to represent another job's build/cache dir.
2. Build an in-memory zip archive containing an entry named `../sibling/pwned.txt` (relative traversal targeting `siblingDir` from `jobDir`).
3. `os.Chdir(jobDir)`, then call `ziplegacy.NewExtractor(reader, size, jobDir)` and `.Extract(ctx)`.
4. Assert: `pwned.txt` should NOT exist in `siblingDir` (i.e., extraction should fail or be rejected) — currently the test will show the file IS written outside `jobDir`, confirming the escape.
5. Contrast with an equivalent test against `tarzstd.NewExtractor`, which should correctly return an error `"... cannot be extracted outside of chroot ..."` for the same traversal payload, proving the inconsistency/missing check in the zip legacy path.

### Citations

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

**File:** shells/abstract.go (L445-458)
```go
func (b *AbstractShell) downloadArtifacts(w ShellWriter, job spec.Dependency, info common.ShellScriptInfo) {
	args := []string{
		"artifacts-downloader",
		"--url",
		info.Build.Runner.URL,
		"--token",
		job.Token,
		"--id",
		strconv.FormatInt(job.ID, 10),
	}

	w.Noticef("Downloading artifacts for %s (%d)...", job.Name, job.ID)
	w.Command(info.RunnerCommand, args...)
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

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L26-32)
```go
func (e *extractor) Extract(ctx context.Context) error {
	zr, err := zip.NewReader(e.r, e.size)
	if err != nil {
		return err
	}

	return archives.ExtractZipArchive(zr)
```

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
