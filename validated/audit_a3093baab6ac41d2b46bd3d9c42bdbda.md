### Title
Path traversal in `cacheArchivePath` allows cache key to escape `CacheDir` - ([File: functions/concrete/run/stages/cache_archive.go])

### Summary
`cacheArchivePath` builds the cache archive file path by joining `e.CacheDir`, the attacker/pipeline-author-controlled `key` (from `CacheSource.Key` / `CacheArchive.Key`, ultimately derived from the job's `cache:key` configuration), and `"cache.zip"`, with no validation that the resulting path stays confined to `CacheDir`. Because `filepath.Join` cleans `..` segments after concatenation, a key containing `../` sequences can make the resolved archive path resolve outside `CacheDir` (and outside the job root), and that path is passed straight through to the `cache-extractor`/`cache-archiver` helper process via `--file`.

### Finding Description
`cacheArchivePath` is: [1](#0-0) 

It performs `filepath.Join(e.CacheDir, key, "cache.zip")` and then makes the result relative to `e.WorkingDir` via `filepath.Rel` — purely for CLI argument presentation, not for path confinement. There is no `filepath.Clean` + prefix check against `e.CacheDir` to reject `..`-laden keys, and no restriction on characters allowed in `key`.

This function is reached from two call sites:
- `CacheExtract.extract` → `s.archivePath(e, src.Key)` → `cacheArchivePath` → `--file` argument to `e.RunnerCommand(ctx, ..., "cache-extractor", "--file", archiveFile, ...)`. [2](#0-1) 
- `CacheArchive.Run` → `s.archivePath(e)` / `s.alternateArchivePath(e)` → `cacheArchivePath` → `--file`/`--alternate-file` to `cache-archiver`. [3](#0-2) 

`src.Key` / `s.Key` originate from the job's `cache:key` field in `.gitlab-ci.yml`, which is pipeline-author-controlled input accepted by the Runner as part of the build data. No sanitization or hashing of the key is performed in this code path except optionally under the `FF_HASH_CACHE_KEYS` feature flag (referenced only in a comment, not enforced here). The existing unit test only exercises a benign key (`"abc123"`) and documents the relative-path behavior, but does not assert any confinement against traversal payloads. [4](#0-3) 

If `key` is e.g. `"../../../../tmp/evil"`, `filepath.Join(e.CacheDir, key, "cache.zip")` cleans to a path several directories above `CacheDir`, and that resolved path — expressed relative to `WorkingDir` — is handed to the helper subprocess, which will read/write the archive file at that resolved location when it runs with its working directory set to `e.WorkingDir`.

### Impact Explanation
A crafted cache key lets a pipeline author cause the `cache-extractor`/`cache-archiver` helper process to open a file path outside the intended `CacheDir` (and outside the job root), resulting in unauthorized file read (during extraction, an attacker-supplied/controlled archive could be written to an arbitrary filesystem location reachable from the resolved relative path) or write (during archiving, cache contents could overwrite a file outside the cache root). This violates the invariant that job-controlled paths must stay confined to the intended cache/build root.

### Likelihood Explanation
Preconditions: the attacker only needs to control the `cache:key` value of a job (a normal, unprivileged pipeline-author capability), and the effective traversal distance depends on how deep `CacheDir` is under the filesystem root/job root — a sufficiently long `../../../..` sequence in the key will escape any fixed-depth `CacheDir`. No existing check in `cacheArchivePath` or its callers blocks this; the function unconditionally trusts `key`. This is straightforward to reproduce deterministically with a unit test.

### Recommendation
In `cacheArchivePath`, after computing `absPath := filepath.Join(e.CacheDir, key, "cache.zip")`, validate confinement before returning, e.g.:
```go
cleanCacheDir := filepath.Clean(e.CacheDir)
if !strings.HasPrefix(filepath.Clean(absPath), cleanCacheDir+string(filepath.Separator)) {
    return "", fmt.Errorf("invalid cache key %q: resolves outside cache directory", key)
}
```
Reject or sanitize keys containing `..`, path separators, or absolute-path prefixes before using them to build filesystem paths, and propagate the error up through `CacheExtract.extract`/`CacheArchive.Run` instead of silently proceeding.

### Proof of Concept
```go
func TestCacheArchivePath_PathTraversalRejected(t *testing.T) {
    e := &env.Env{WorkingDir: "/builds/group/project", CacheDir: "/cache"}
    maliciousKeys := []string{
        "../../../../etc/cron.d/evil",
        "../../../tmp/pwn",
        "..\\..\\..\\Windows\\System32\\evil",
    }
    for _, key := range maliciousKeys {
        got := cacheArchivePath(e, key) // current implementation
        abs, _ := filepath.Abs(filepath.Join(e.WorkingDir, got))
        assert.True(t, strings.HasPrefix(abs, filepath.Clean(e.CacheDir)+string(filepath.Separator)),
            "archive path %q escaped CacheDir for key %q", abs, key)
    }
}
```
Expected today: the assertion fails, proving the resolved path escapes `e.CacheDir` for traversal-style keys — confirming the missing confinement check.

### Citations

**File:** functions/concrete/run/stages/cache_archive.go (L45-55)
```go
	archiveFile := s.archivePath(e)

	args := []string{
		"cache-archiver",
		"--file", archiveFile,
		"--timeout", strconv.Itoa(s.Timeout),
	}

	if s.AlternateKey != "" && s.AlternateKey != s.Key {
		args = append(args, "--alternate-file", s.alternateArchivePath(e))
	}
```

**File:** functions/concrete/run/stages/cache_archive.go (L114-121)
```go
func cacheArchivePath(e *env.Env, key string) string {
	absPath := filepath.Join(e.CacheDir, key, "cache.zip")
	rel, err := filepath.Rel(e.WorkingDir, absPath)
	if err != nil {
		return absPath
	}
	return rel
}
```

**File:** functions/concrete/run/stages/cache_extract.go (L81-129)
```go
func (s CacheExtract) extract(ctx context.Context, e *env.Env, src CacheSource) error {
	archiveFile := s.archivePath(e, src.Key)

	args := []string{
		"cache-extractor",
		"--file", archiveFile,
		"--timeout", strconv.Itoa(s.Timeout),
	}

	desc := src.Descriptor
	if desc.URL != "" {
		if desc.GoCloudURL {
			args = append(args, "--gocloud-url", desc.URL)
		} else {
			args = append(args, "--url", desc.URL)
		}
	}
	if desc.HeadURL != "" {
		args = append(args, "--head-url", desc.HeadURL)
	}

	alt := src.AlternateDescriptor
	if alt.URL != "" {
		if alt.GoCloudURL {
			args = append(args, "--alternate-gocloud-url", alt.URL)
		} else {
			args = append(args, "--alternate-url", alt.URL)
		}
		if alt.HeadURL != "" {
			args = append(args, "--alternate-head-url", alt.HeadURL)
		}
	}

	// --header is upload-only; cache-extractor doesn't accept it.
	_ = desc.Headers

	// Primary wins on key collision: cache-extractor sees a single env
	// per invocation, so the URL it attempts first must keep its own
	// credentials. alt's env only fills keys the primary descriptor
	// didn't set.
	envOverlay := make(map[string]string, len(desc.Env)+len(alt.Env))
	maps.Copy(envOverlay, alt.Env)
	maps.Copy(envOverlay, desc.Env)
	return e.RunnerCommand(ctx, e.HelperEnvs(envOverlay), args...)
}

func (s CacheExtract) archivePath(e *env.Env, key string) string {
	return cacheArchivePath(e, key)
}
```

**File:** functions/concrete/run/stages/cache_archive_test.go (L14-34)
```go
func TestCacheArchivePath(t *testing.T) {
	e := &env.Env{
		WorkingDir: "/builds/group/project",
		CacheDir:   "/cache",
	}

	// archivePath uses filepath.Rel, which returns OS-native
	// separators (backslash on Windows). Convert the expected
	// POSIX form so the test passes on both.
	want := filepath.FromSlash("../../../cache/abc123/cache.zip")

	t.Run("CacheArchive returns path relative to WorkingDir", func(t *testing.T) {
		s := CacheArchive{Key: "abc123"}
		assert.Equal(t, want, s.archivePath(e))
	})

	t.Run("CacheExtract returns path relative to WorkingDir", func(t *testing.T) {
		s := CacheExtract{}
		assert.Equal(t, want, s.archivePath(e, "abc123"))
	})
}
```
