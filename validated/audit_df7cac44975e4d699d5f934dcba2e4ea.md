This confirms the analysis. `errorIfGitDirectory` only checks for `.git` prefix (and only warns, doesn't block); there is no check preventing `../` traversal or absolute paths, and no chroot/prefix validation like the one present in `tarzstd_extractor.go`.

### Title
Zip extraction (`ExtractZipArchive`/`extractZipFile`) has no path-traversal or containment check, allowing artifact/cache archives to write files outside the intended extraction root - (File: helpers/archives/zip_extract.go)

### Summary
`Extract` in `commands/helpers/archive/ziplegacy/zip_legacy_extractor.go` delegates directly to `archives.ExtractZipArchive`, which writes each zip entry using the raw `file.Name` from the archive with no path sanitization, no `..`/absolute-path rejection, and no containment check against the target directory. This differs from the `tarzstd` extractor (`commands/helpers/archive/tarzstd/tarzstd_extractor.go`), which explicitly resolves `filepath.Abs(filepath.Join(e.dir, hdr.Name))` and rejects entries escaping `e.dir`. As a result, a malicious zip cache/artifact can restore files to arbitrary paths reachable by the runner process (e.g. `.git/hooks/*`, files sourced or executed by later job stages, or CI_PROJECT_DIR-adjacent locations), independent of the target `dir` argument, which is entirely unused by this code path.

### Finding Description
`Extract` (`commands/helpers/archive/ziplegacy/zip_legacy_extractor.go:26-33`) ignores `e.dir` and calls `archives.ExtractZipArchive(zr)` [1](#0-0) . Inside `ExtractZipArchive` (`helpers/archives/zip_extract.go:85-110`), each `zip.File` is passed to `extractZipFile`, which builds the write target directly from `file.Name` with `filepath.Dir(file.Name)` for `MkdirAll` and `file.Name` itself for `os.Create`/`os.Symlink`, with no `filepath.Abs`/prefix check against any base directory [2](#0-1) . The only guard, `errorIfGitDirectory`, checks only whether the entry begins with `.git` and merely logs a warning rather than blocking extraction, and it does nothing to stop `../` segments, absolute paths, or symlink targets [3](#0-2) . `extractZipSymlinkEntry` further lets the archive define arbitrary symlink targets (`os.Symlink(string(data), file.Name)`) with the link-content taken directly from the archive entry’s file contents [4](#0-3) . By contrast, the tar+zstd extractor computes an absolute path and explicitly rejects any entry resolving outside `e.dir`: `if !strings.HasPrefix(path, e.dir+string(filepath.Separator)) && path != e.dir { return fmt.Errorf(...) }` [5](#0-4) . The zip (legacy) path has no equivalent check.

Both artifact download and cache extraction commands construct the extractor with the intended working directory as `dir`, e.g. `archive.NewExtractor(format, f, size, wd)` in `commands/helpers/artifacts_downloader.go:131` and `commands/helpers/cache_extractor.go:655`, and rely on the extractor to confine writes to that directory [6](#0-5) [7](#0-6) . For the zip (legacy) format, that `dir`/`wd` value is silently unused, so extraction is effectively confined only by the current working directory plus whatever relative/absolute path is embedded in the attacker-supplied zip entry names — i.e., not confined at all.

Attacker-controlled inputs: a pipeline author (or an attacker whose branch/MR produces a job whose artifacts/cache get consumed by a later job, including in a different stage of the same pipeline) fully controls the artifact/cache archive's bytes, including zip entry names (`file.Name`), which can contain `../../` sequences or be effectively arbitrary, and symlink entry link targets/content.

Exploit flow:
1. Job A creates an artifact/cache with a crafted zip containing an entry named e.g. `../scripts/deploy.sh`, `../../.git/hooks/post-checkout`, or a path matching a file that a later stage's generated shell script sources/executes (e.g., a name colliding with `CI_PROJECT_DIR`-relative files the runner or job script trusts).
2. Job B (later stage/job) downloads and extracts that artifact/cache via `gitlab-runner artifact-downloader`/`cache-extractor`, invoking the legacy zip extractor.
3. `extractZipFile` writes to `file.Name` verbatim, escaping the intended build directory, since no containment check exists for zip.
4. If the overwritten/created file is later executed or sourced by the job's generated shell script, git hooks, or another trusted step, attacker bytes execute with the job's privileges.

### Impact Explanation
This allows a normal, unprivileged pipeline author to have artifact/cache extraction plant a file at a path outside the intended artifact/cache root — including files that later pipeline stages implicitly execute or source (build scripts, git hooks, generated environment files) — leading to code execution in the context of a subsequent job/stage or exposure/corruption of files outside the sandboxed extraction directory. This matches the "stronger-context execution" impact category since it lets one stage's untrusted archive content influence what a later, more-trusted stage executes.

### Likelihood Explanation
Feasibility is high and fully repeatable: no special runner configuration, executor privileges, or admin cooperation is needed. Any user who can define pipeline stages/jobs producing and consuming artifacts or cache (a very common, always-available capability) can craft the archive server-side is not required — the zip content is attacker-controlled end-to-end since it's produced by the earlier job the same attacker authored. The only requirement is that the legacy zip extractor code path is used (as opposed to `fastzip`, which delegates path handling to the `saracen/fastzip` library and may have its own protections not reviewed here).

### Recommendation
Add the same containment check used in `tarzstd_extractor.go` to the zip extraction path: resolve `filepath.Abs(filepath.Join(dir, file.Name))` for every `zip.File` before any `MkdirAll`/`Create`/`Symlink` call in `extractZipFile`/`ExtractZipArchive`, and reject (or skip with a warning) any entry whose resolved path is not `dir` or does not have `dir + separator` as a prefix. Additionally, ensure `ziplegacy.extractor.Extract` actually passes/uses `e.dir` as the extraction root rather than relying on the process's current working directory, and validate/reject symlink entries whose target escapes the same root.

### Proof of Concept
Go unit test plan (extending `helpers/archives/zip_extract_test.go`):
1. Build a zip archive containing a single entry named `../evil.sh` (or on the target OS, `..\\evil.sh`) with attacker-controlled content, using `zip.Writer`.
2. Call `archives.ExtractZipArchive` (or `ExtractZipFile`) with the current directory set to a temp subdirectory `outDir/nested`.
3. Assert that `filepath.Join(outDir, "evil.sh")` (one level above the intended extraction root) was created and contains the attacker content — proving the traversal — where a correctly-fixed implementation would instead return an error such as `"cannot be extracted outside of chroot"` (matching the check already present in `tarzstd_extractor.go`).
4. As a stronger integration PoC, name the entry so it overwrites a file path that a subsequent `commands/helpers/artifacts_downloader.go`/`cache_extractor.go`-driven job stage would source or execute (e.g., a generated `.git/hooks/post-checkout` under the same `CI_PROJECT_DIR`), then run the second stage and assert the injected script executed.

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

**File:** commands/helpers/artifacts_downloader.go (L125-141)
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
}
```

**File:** commands/helpers/cache_extractor.go (L646-663)
```go
	f, size, format, err := openArchive(c.File)
	if os.IsNotExist(err) {
		warningln("Cache file does not exist")
	}
	if err != nil {
		logrus.Fatalln(err)
	}
	defer f.Close()

	extractor, err := archive.NewExtractor(format, f, size, wd)
	if err != nil {
		logrus.Fatalln(err)
	}

	err = extractor.Extract(context.Background())
	if err != nil {
		logrus.Fatalln(err)
	}
```
