### Title
Missing `.git` control-path guard in tar+zstd extractor allows cache/artifact restore to overwrite git hooks/config - (File: commands/helpers/archive/tarzstd/tarzstd_extractor.go)

### Summary
`extractor.Extract` in `commands/helpers/archive/tarzstd/tarzstd_extractor.go` only validates that an archive entry's resolved path stays within the extraction root (`e.dir`); it applies no check against writing into `.git` (hooks, config, etc.). The legacy zip extraction path (`helpers/archives/zip_extract.go`, `zip_create.go`) explicitly calls `errorIfGitDirectory`/`isPathAGitDirectory` (`helpers/archives/path_check_helper.go`) to reject any `.git/...` entry, but the newer tarzstd extractor used for cache/artifact restore has no equivalent guard.

### Finding Description
`Extract` computes `path = filepath.Abs(filepath.Join(e.dir, hdr.Name))` and only checks `strings.HasPrefix(path, e.dir+separator)` to stop directory-traversal escapes (`commands/helpers/archive/tarzstd/tarzstd_extractor.go:57-64`). There is no denylist for repo-control paths such as `.git/hooks/*`, `.git/config`, `.git/info/*`, etc. Since GitLab CI cache/artifact `paths:` (and thus what gets archived and later restored) are attacker-controlled fields in `.gitlab-ci.yml`/job scripts (`commands/helpers/file_archiver.go`, `functions/concrete/run/stages/artifact_upload.go`), and since `writeRestoreCacheScript`/`cacheExtractor` (`shells/abstract.go:1348-1358`, `223-283`) restore into the job's build directory (`writeCdBuildDir`) which already contains a live `.git` checkout from the preceding `get_sources` stage, an attacker can:
1. Create/modify a job whose cache or artifact `paths` include `.git/hooks/post-checkout` (or `.git/config`) with malicious content.
2. Push/run that job so the cache (or artifact, if reused by a downstream job in the same pipeline) is stored under a cache key that a higher-privilege pipeline (e.g., a protected-branch job with elevated CI/CD variables or push credentials) will also use.
3. When the protected pipeline restores that cache/artifact via `tarzstd.Extract`, the archive entries for `.git/hooks/...` or `.git/config` are written straight into the already-checked-out repository, because the chroot check permits any path under `e.dir`, including `.git`.
4. Any later git operation performed by the job (commit, push, checkout, fetch — common in release/automation jobs) can then trigger the planted hook or honor the poisoned config (e.g. `core.hooksPath`, `insteadOf` URL rewrite, `include.path`), executing attacker code or redirecting credentials in the context of the higher-trust job.

The comparable legacy zip code path already defends against exactly this by rejecting `.git`-prefixed entries (`errorIfGitDirectory` in `helpers/archives/path_check_helper.go`, invoked from `helpers/archives/zip_extract.go` and `zip_create.go`), which shows this is a recognized risk class in this codebase that was not carried over to the tarzstd implementation.

### Impact Explanation
A job with attacker-controlled cache/artifact content can plant executable git hooks or rewrite `.git/config` in the shared build directory. If that cache/artifact key is later consumed by a higher-privilege (e.g., protected-branch) job that performs git operations, this yields code execution or credential/URL redirection in that job's context — protected-ref escalation and credential misuse, matching the intended scoped impact.

### Likelihood Explanation
Preconditions: cache/artifact key collision (or intentional cache-key reuse) between an attacker-influenced job and a higher-trust job, and a later job performing git operations (commit/push/checkout) after cache/artifact restoration. Cache key collisions across branches/pipelines are common when default or hand-picked cache keys aren't strictly scoped per protected ref, and `cache:paths`/`artifacts:paths` accepting `.git/**` is not blocked anywhere in the tarzstd path. This is fully reproducible with a Go unit test and requires no admin/leaked-key assumptions — only normal job configuration control by an unprivileged pipeline author.

### Recommendation
Add the same `.git` control-path rejection used in the legacy zip path (`helpers/archives/path_check_helper.go`'s `errorIfGitDirectory`/`isPathAGitDirectory`) to `tarzstd.extractor.Extract` (and audit `fastzip`/`ziplegacy`/`gziplegacy`/`raw` extractors for the same gap), rejecting or warning on any entry whose first path segment is `.git` before it is written to disk.

### Proof of Concept
Go unit test in `commands/helpers/archive/tarzstd/tarzstd_extractor_test.go`:
1. Build an in-memory tar+zstd stream containing an entry `.git/hooks/post-checkout` with executable mode and a shell payload (e.g., `#!/bin/sh\ntouch pwned`).
2. Call `NewExtractor(reader, size, tmpDir)` then `Extract(ctx)`.
3. Assert that the file `tmpDir/.git/hooks/post-checkout` was NOT created (expected fixed behavior) — currently the test would show it IS created and executable, demonstrating the gap.
4. Cross-check against `helpers/archives/zip_extract_test.go` to confirm the equivalent `.git` entry is already rejected there, highlighting the inconsistency. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** shells/abstract.go (L223-264)
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
	}
```

**File:** shells/abstract.go (L1348-1358)
```go
func (b *AbstractShell) writeRestoreCacheScript(
	ctx context.Context,
	w ShellWriter,
	info common.ShellScriptInfo,
) error {
	b.writeExports(w, info)
	b.writeCdBuildDir(w, info)

	// Try to restore from main cache, if not found cache for default branch
	return b.cacheExtractor(ctx, w, info)
}
```

**File:** functions/concrete/run/stages/artifact_upload.go (L132-148)
```go
func (s ArtifactUpload) archiverArgs() []string {
	var args []string

	for _, p := range s.Paths {
		args = append(args, "--path", p)
	}

	for _, p := range s.Exclude {
		args = append(args, "--exclude", p)
	}

	if s.Untracked {
		args = append(args, "--untracked")
	}

	return args
}
```
