Confirmed: the call chain `Runner.prepare` → `stages.CacheExtract.Run`/`stages.ArtifactDownload.Run` invokes `artifacts-downloader`/`cache-extractor` helper commands, which call `archive.NewExtractor` and `extractor.Extract` directly on downloaded, attacker-influenced archive content [1](#0-0) [2](#0-1) [3](#0-2) .

### Title
Symlink target (Linkname) unvalidated in tar+zstd extractor allows arbitrary-path file write via deferred symlink - ([File: commands/helpers/archive/tarzstd/tarzstd_extractor.go])

### Summary
`extractor.Extract` in `tarzstd_extractor.go` validates that a symlink entry's own on-disk path stays within `e.dir`, but never validates `hdr.Linkname` (the symlink's target). The symlink is created via `os.Symlink(hdr.Linkname, path)`, and any subsequent regular-file entry (in the same or a later extraction into the same directory) that reuses that path will be written through `os.Create(path)`, which follows the symlink and writes to the attacker-chosen target outside `e.dir`.

### Finding Description
In `Extract`, for every header the code computes `path = filepath.Abs(filepath.Join(e.dir, hdr.Name))` and checks `strings.HasPrefix(path, e.dir+separator)` [4](#0-3) . This check only constrains where the *symlink file itself* is placed, not what it points to. Symlink entries are deferred into a map and only materialized after the main loop, via `os.Symlink(hdr.Linkname, path)`, with `hdr.Linkname` used verbatim — no prefix check, no `..` resolution check, no restriction to relative-within-`e.dir` targets [5](#0-4) .

Because symlink creation is deferred to the end of the archive, a regular file entry with the same name inside the *same* archive would just be created as a normal file first (the symlink doesn't exist yet), and the later `os.Symlink` call would then fail with "file exists" — so intra-archive exploitation is blocked. However, once `Extract` completes, the dangling symlink (e.g. `e.dir/foo -> /etc/cron.d/x`) persists on disk under `e.dir`. Both `CacheExtractorCommand.Execute` and `ArtifactsDownloaderCommand.Execute` extract directly into the job's working directory (`wd`) [3](#0-2) [2](#0-1) , and `Runner.prepare` runs multiple `CacheExtract`/`ArtifactDownload` stages into that same directory across a job's `restore_cache`/`download_artifacts` phases [6](#0-5) . A subsequent cache or artifact archive (a different `cache: key`, or a dependency artifact) that contains a regular file entry named `foo` will have its content written via `os.Create(path)`, which transparently follows the pre-existing symlink and writes to `/etc/cron.d/x` instead of `e.dir/foo`. The path-prefix check on the second entry passes because it only inspects the literal joined string `e.dir/foo`, not what currently resides on the filesystem at that location.

### Impact Explanation
An attacker who can poison one cache/artifact archive consumed by a job (e.g., by pushing to a branch/project that shares a cache key, or by controlling an earlier job stage that produces an artifact/cache) can plant a dangling symlink escaping `e.dir`. A second archive consumed later in the same or another job then writes arbitrary content to the symlink target path on the runner host/container, resulting in a file write outside the intended build/cache/artifact root — matching the scoped impact exactly.

### Likelihood Explanation
Feasible and repeatable: cache poisoning across shared cache keys (e.g. same key used across branches/MRs in a project) is a documented weak boundary in GitLab CI, and artifacts from a dependency job in the same pipeline/project are fully attacker-controlled if the attacker controls that job's script. No privileged access is required — only the ability to shape the contents of a cache or artifact archive that another job stage later extracts into the same working directory.

### Recommendation
Validate `hdr.Linkname` before calling `os.Symlink`: resolve `filepath.Join(path_dir, hdr.Linkname)` (for relative links) or reject absolute `Linkname` outright, and enforce the same `strings.HasPrefix(resolved, e.dir+separator)` containment check applied to `hdr.Name`. Additionally, before creating a regular file at `path`, use `os.Lstat` to detect if an existing filesystem entry at `path` is a symlink and refuse to write through it (e.g., remove/reject rather than following it), closing the deferred-symlink write bypass across extraction calls.

### Proof of Concept
Go unit test in `commands/helpers/archive/tarzstd` package:
```go
func TestExtract_SymlinkTargetEscape(t *testing.T) {
    dir := t.TempDir()
    outside := t.TempDir()
    targetFile := filepath.Join(outside, "pwned")

    // Archive 1: symlink "link" -> outside/pwned
    buf1 := buildTarZstd(t, []tarEntry{
        {name: "link", linkname: targetFile, typeflag: tar.TypeSymlink, mode: 0777 | int64(os.ModeSymlink)},
    })
    ext1, _ := archive.NewExtractor(archive.TarZstd, bytes.NewReader(buf1), int64(len(buf1)), dir)
    require.NoError(t, ext1.Extract(context.Background()))

    // Archive 2 (simulating a later cache/artifact extraction into same dir):
    // regular file entry reusing the same name "link"
    buf2 := buildTarZstd(t, []tarEntry{
        {name: "link", content: []byte("attacker content"), typeflag: tar.TypeReg},
    })
    ext2, _ := archive.NewExtractor(archive.TarZstd, bytes.NewReader(buf2), int64(len(buf2)), dir)
    err := ext2.Extract(context.Background())

    // Assert the write did NOT escape to targetFile
    _, statErr := os.Stat(targetFile)
    assert.True(t, os.IsNotExist(statErr) || err != nil,
        "regular file write must not follow a symlink to outside %s, but wrote to %s", dir, targetFile)
}
```
Expected (buggy) behavior: `targetFile` gets created with content `"attacker content"` and `err` is `nil`, proving the escape. Expected (fixed) behavior: either `ext1.Extract` rejects the out-of-`e.dir` `Linkname` at symlink-creation time, or `ext2.Extract` detects the pre-existing symlink at `path` and refuses to follow it.

### Citations

**File:** functions/concrete/run/runner.go (L172-204)
```go
//nolint:gocognit
func (r *Runner) prepare(ctx context.Context) error {
	if err := r.section(ctx, "get_sources", r.config.GetSources.Run); err != nil {
		return fmt.Errorf("fetching sources: %w", err)
	}

	// Reload so KEY=VALUE entries appended by pre_clone_script / post_clone_script flow into downstream stages.
	if hasCacheSources(r.config.CacheExtract) {
		r.loadGitlabEnv()
		_ = r.section(ctx, "restore_cache", func(ctx context.Context, e *env.Env) error {
			for _, cache := range r.config.CacheExtract {
				if len(cache.Sources) == 0 {
					continue
				}
				if err := cache.Run(ctx, e); err != nil {
					r.logWarningf("Failed to restore cache %q: %v", cache.Sources[0].Key, err)
				}
			}
			return nil
		})
	}

	if len(r.config.ArtifactExtract) > 0 {
		r.loadGitlabEnv()
		_ = r.section(ctx, "download_artifacts", func(ctx context.Context, e *env.Env) error {
			for _, artifact := range r.config.ArtifactExtract {
				if err := artifact.Run(ctx, e); err != nil {
					r.logWarningf("Failed to download artifact %q: %v", artifact.ArtifactName, err)
				}
			}
			return nil
		})
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

**File:** commands/helpers/archive/tarzstd/tarzstd_extractor.go (L107-121)
```go
	for path, hdr := range deferred {
		fi := hdr.FileInfo()
		if fi.Mode()&os.ModeSymlink == 0 && !fi.Mode().IsDir() {
			continue
		}

		if fi.Mode()&os.ModeSymlink != 0 {
			if err := os.Symlink(hdr.Linkname, path); err != nil {
				return err
			}
		}

		if err := e.updateFileMetadata(path, hdr); err != nil {
			return err
		}
```
