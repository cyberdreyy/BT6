### Title
Legacy zip cache/artifact extractor ignores the target directory and performs no path-containment check, enabling zip-slip escape from the assigned cache/build root - (File: helpers/archives/zip_extract.go, commands/helpers/archive/ziplegacy/zip_legacy_extractor.go)

### Summary
The zip extraction path used by `CacheExtractorCommand` writes archive entries directly to `file.Name` with no root/chroot validation, unlike the tar+zstd extractor which enforces `strings.HasPrefix(path, e.dir+separator)`. A cache archive whose entries contain `../` traversal or symlink targets pointing outside the intended cache/build directory will be extracted exactly where the attacker specifies, breaking the invariant that cache restore stays within the assigned root.

### Finding Description
`AbstractShell.cacheExtractor` builds `--path` args from job-defined `cacheOptions.Paths` and, via `newCacheConfig`, computes an `ArchiveFile` under `build.CacheDir` [1](#0-0) . The actual extraction happens in the helper's `CacheExtractorCommand.Execute`, which resolves the archive format and calls `archive.NewExtractor(format, f, size, wd)` followed by `extractor.Extract(ctx)` [2](#0-1) .

For the tar+zstd format, the extractor computes each entry's absolute path and explicitly rejects anything that would land outside `e.dir`: [3](#0-2) 

For the legacy zip format (still a supported/default archive format, exercised by `TestCacheExtractorValidArchive` via `OnEachZipExtractor`), `ziplegacy.extractor.Extract` completely discards the `dir` parameter passed to `NewExtractor` and hands the zip reader straight to `archives.ExtractZipArchive`: [4](#0-3) 

`ExtractZipArchive`/`extractZipFile` then use `file.Name` verbatim — no `filepath.Join` against a root, no `filepath.Clean`, no prefix/containment check, and no rejection of absolute paths or `..` segments, for directories, files, or symlinks: [5](#0-4) 

Compared to the tarzstd path, this is a clear asymmetry/regression: one format enforces root containment, the other does not. Any zip-format cache archive with a crafted entry name (e.g. `../../../.git/hooks/pre-commit`, or a symlink entry whose target escapes the working directory followed by a nested write) will be written wherever `os.MkdirAll`/`os.OpenFile`/`os.Symlink` resolves it on the filesystem the helper process can reach, not confined to `build.CacheDir`/`build.RootDir`.

### Impact Explanation
An attacker who controls the contents of a cache archive that will later be restored (via a pipeline they author, using a cache key/fallback key they choose, in a project where they can run jobs) can escape the extraction root and overwrite arbitrary files reachable by the runner helper process — e.g. build scripts, git hooks, other cached artifacts, or files consumed by a later, higher-privilege pipeline stage. This matches the "path-root escape and later stronger-context overwrite" impact class described in the question.

### Likelihood Explanation
Preconditions are minimal and fully attacker-controlled: any unprivileged pipeline author can define `cache:key`/`cache:paths` in `.gitlab-ci.yml`, cause a cache archive to be produced and restored in a subsequent job, and the vulnerable zip-extraction path is a supported, tested code path (`ziplegacy`) reached whenever the zip format is selected for cache/artifact extraction. No admin privileges, no cluster compromise, and no external service compromise are required — only the ability to run repeated jobs and control cache contents, exactly as listed as attacker capabilities in the question.

### Recommendation
Add the same containment enforcement used in `tarzstd_extractor.go` to the zip extraction path: resolve each `file.Name` against the target `dir` with `filepath.Join`/`filepath.Abs`, reject any resulting path that is not a strict descendant of `dir` (and reject symlink targets that resolve outside `dir`), and thread the `dir` parameter through `ziplegacy.extractor.Extract` into `archives.ExtractZipArchive`/`extractZipFile` instead of discarding it.

### Proof of Concept
Go unit test (extend `zip_extract_test.go` / `cache_extractor_test.go` pattern):
1. Create a temporary directory `root` and a sibling directory `victim` (outside `root`).
2. Build a zip archive containing an entry named `../victim/pwned.txt` with attacker-controlled content.
3. Call `ziplegacy.NewExtractor(reader, size, root)` then `Extract(ctx)` (or drive it through `CacheExtractorCommand.Execute` with `wd = root`).
4. Assert: `os.Stat(filepath.Join(root, "../victim/pwned.txt"))` (i.e. `victim/pwned.txt`) exists with attacker content — proving the file was written outside `root`.
5. Contrast with an equivalent tarzstd archive containing the same traversal entry and assert `Extract` returns the `"cannot be extracted outside of chroot"` error, showing the inconsistency between the two extractors.

### Citations

**File:** shells/abstract.go (L223-263)
```go
func (b *AbstractShell) cacheExtractor(ctx context.Context, w ShellWriter, info common.ShellScriptInfo) error {
	skipRestoreCache := true

	for _, cacheOptions := range info.Build.Cache {
		// Create list of files to extract
		var archiverArgs []string
		for _, path := range cacheOptions.Paths {
			archiverArgs = append(archiverArgs, "--path", path)
		}

		if cacheOptions.Untracked {
			archiverArgs = append(archiverArgs, "--untracked")
		}

		// Skip restoring cache if no cache is defined
		if len(archiverArgs) < 1 {
			continue
		}

		skipRestoreCache = false

		// Skip extraction if no cache is defined
		cacheConfig, warning, err := newCacheConfig(info.Build, cacheOptions.Key)
		if warning != "" {
			w.Warningf("%s", warning)
		}
		if err != nil {
			w.Noticef("Skipping cache extraction due to %v", err)
			continue
		}

		cacheOptions.Policy = spec.CachePolicy(info.Build.GetAllVariables().ExpandValue(string(cacheOptions.Policy)))

		if ok, err := cacheOptions.CheckPolicy(spec.CachePolicyPull); err != nil {
			return fmt.Errorf("%w for %s", err, cacheConfig.HumanKey)
		} else if !ok {
			w.Noticef("Not downloading cache %s due to policy", cacheConfig.HumanKey)
			continue
		}

		b.extractCacheOrFallbackCachesWrapper(ctx, w, info, *cacheConfig, cacheOptions)
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

**File:** helpers/archives/zip_extract.go (L12-83)
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
