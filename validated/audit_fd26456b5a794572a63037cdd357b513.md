### Title
Zip artifact extraction lacks path-traversal containment, allowing writes/symlinks outside the build directory - (File: helpers/archives/zip_extract.go)

### Summary
`helpers/archives/zip_extract.go` extracts zip entries by writing directly to `file.Name` (via `os.OpenFile`/`os.MkdirAll`/`os.Symlink`) with no path-containment check, unlike the tar+zstd extractor which explicitly validates each entry path stays under the destination root. Because this code is reached from the `artifacts-downloader` command's default Zip codepath, a job can produce/upload an artifact zip with `../`-traversing or absolute entry names/symlink targets that get written outside the intended job working directory when a dependent job downloads and extracts it.

### Finding Description
- `extractZipFile` / `extractZipFileEntry` / `extractZipSymlinkEntry` in `helpers/archives/zip_extract.go` use `file.Name` verbatim: `os.MkdirAll(filepath.Dir(file.Name), 0o777)` (`zip_extract.go:63`), `os.OpenFile(file.Name, ...)` (`zip_extract.go:51`), and `os.Symlink(string(data), file.Name)` (`zip_extract.go:37`). There is no `filepath.Abs`/`HasPrefix` containment check anywhere in this file. [1](#0-0) 
- Contrast with `commands/helpers/archive/tarzstd/tarzstd_extractor.go`, which explicitly resolves `path, err = filepath.Abs(filepath.Join(e.dir, hdr.Name))` and rejects it with `"cannot be extracted outside of chroot"` if it doesn't stay under `e.dir` — this protection is entirely absent from the zip path. [2](#0-1) 
- `ExtractZipArchive` iterates `archive.File` and calls `extractZipFile(file)` for every entry without any name normalization/validation (only a git-directory-name warning check unrelated to traversal). [3](#0-2) 
- Reachability: `commands/helpers/artifacts_downloader.go`'s `Execute` downloads the job's artifacts archive and calls `archive.NewExtractor(format, f, size, wd)`; for the default `archive.Zip` format this resolves to `ziplegacy.NewExtractor`, whose `Extract` calls `archives.ExtractZipArchive(zr)` directly — i.e., the vulnerable code — with `wd` (the job's working directory) intended as the containment root but never actually enforced. [4](#0-3) [5](#0-4) 
- Attacker-controlled input: a pipeline author fully controls the contents of `artifacts:` produced by their own job (e.g., by crafting arbitrary files/zip via a `zip` binary or a custom archiver before GitLab Runner re-uploads, or — more directly — since GitLab CI supports `artifacts:` on raw files that the Runner itself zips, an attacker with any means of getting a crafted `.zip` accepted as the job's artifact archive can embed entries named `../../../etc/passgrunner`, absolute paths (`/tmp/x`), or symlink entries pointing outside `wd`). When a downstream job with `dependencies:`/`needs:` on that job runs `artifacts-downloader`, extraction happens under the *new* job's working directory with no path check, allowing writes outside that directory.
- Existing checks reviewed: only `errorIfGitDirectory` (blocks entries starting with `.git/`, unrelated to traversal) and a `pathErrorTracker` that suppresses repeated *error* logging — neither validates or blocks `../` or absolute paths. No `filepath.Clean`/`filepath.Rel`/prefix check exists anywhere in the zip package.

### Impact Explanation
An unprivileged pipeline author can cause the Runner process (running as the CI job's OS user, e.g., under a shell/docker/kubernetes executor) to write or overwrite arbitrary files, or create arbitrary symlinks, outside the intended build/artifact root when the crafted artifact is extracted by any job in a pipeline that consumes it (same or downstream job). This can corrupt runner-managed build directories on shared runners, overwrite files the job process has permission to write, or plant symlinks that redirect subsequent legitimate writes elsewhere — a directory-traversal / path-confinement violation matching the "file operations must stay within intended build/cache/artifact roots" invariant.

### Likelihood Explanation
High feasibility and fully within an unprivileged pipeline author's control: no special runner configuration, admin privileges, or race condition is required — only the ability to control the byte content of a zip file that ends up being used as/decoded as the job's artifacts archive, and a downstream job (which the same pipeline author also controls) that downloads and extracts it via `artifacts-downloader`. The bug is deterministic and repeatable on every extraction of a crafted archive.

### Recommendation
Add the same containment check used in `tarzstd_extractor.go` to the zip extraction path: resolve each `file.Name` against the destination root with `filepath.Abs(filepath.Join(destRoot, file.Name))`, reject entries where the resolved path does not stay under `destRoot` (via `strings.HasPrefix(path, destRoot+string(filepath.Separator))`), and apply the same check for symlink targets (reject/normalize absolute symlink targets or targets that resolve outside `destRoot`). Thread `destRoot` (currently only implicit as `wd`) explicitly into `ExtractZipArchive`/`extractZipFile` rather than relying on `file.Name` alone.

### Proof of Concept
Go unit test in `helpers/archives/zip_extract_test.go`:
1. Build an in-memory `zip.Writer` with entries named `"../evil.txt"` and (on non-Windows) a symlink entry named `"link"` whose target data is `"../../outside"`.
2. Create a temp directory `root`, `chdir` into it (mirroring current extraction behavior which uses relative `file.Name` against CWD), call `archives.ExtractZipArchive(&zr.Reader)`.
3. Assert: `_, err := os.Stat(filepath.Join(root, "..", "evil.txt")); err == nil` currently succeeds (proving escape) — after the fix, the extraction should return an error and `evil.txt` should not exist outside `root`.
4. Add fuzz test `FuzzExtractZipFile` seeding `zip.File.Name` with `../../etc/x`, absolute paths, and symlink targets, asserting (post-fix) that no resulting file/symlink path ever falls outside a fixed temp chroot directory.

### Citations

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

**File:** helpers/archives/zip_extract.go (L85-97)
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

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L26-32)
```go
func (e *extractor) Extract(ctx context.Context) error {
	zr, err := zip.NewReader(e.r, e.size)
	if err != nil {
		return err
	}

	return archives.ExtractZipArchive(zr)
```
