### Title
Unsanitized alternate cache key allows cache archive path traversal outside CacheDir when `FF_HASH_CACHE_KEYS` is enabled - (File: shells/abstract.go)

### Summary
When `FF_HASH_CACHE_KEYS` is enabled, `newCacheConfig` skips `cachekey.Sanitize` entirely and computes the "alternate" (unhashed/back-compat) archive path directly from the raw, attacker-controlled `humanKey`. Because `getArchivePath` builds the file path with `path.Join(build.CacheDir, key, "cache.zip")` and performs no post-join containment check, a job-supplied cache key containing `../` segments can make `AlternateArchiveFile` resolve outside `build.CacheDir`.

### Finding Description
`newCacheConfig` in `shells/abstract.go` derives `humanKey` from the job-controlled `cacheOptions.Key`, expanded via `build.GetAllVariables().ExpandValue(userKey)`: [1](#0-0) 

Sanitization is conditionally bypassed based on the feature flag: [2](#0-1) 

When `featureflags.HashCacheKeys` is on, `sanitizer` becomes a no-op that returns the raw string unchanged, so `humanKey` retains any `../` sequences from `cacheOptions.Key`.

The primary archive path uses `hashedKey = hasher(humanKey)` (a SHA-256 hex digest), which is safe regardless of traversal content. However, the "alternate" key — kept for backward-compatibility with the other naming scheme — is computed by `cacheAlternateKey`: [3](#0-2) 

When `hashEnabled` is true, this function returns `humanKey` **unhashed and unsanitized**. That value is then passed straight into `getArchivePath`: [4](#0-3) [5](#0-4) 

`getArchivePath` builds `file := path.Join(build.CacheDir, key, "cache.zip")` with no check that the joined result stays under `build.CacheDir` (unlike the analogous remote-cache code in `cache/cache.go`'s `GetAdapter`, which explicitly validates `strings.HasPrefix(fullPath, basePath+"/")` after the join — a check that is absent here). The subsequent `filepath.Rel(build.BuildDir, file)` call only re-expresses the (already-escaped) absolute path relative to the build directory; it does not constrain or reject paths outside `CacheDir`.

Because `path.Join` lexically collapses `..` segments, a key such as `../../../outside/cache` can push `AlternateArchiveFile` outside `CacheDir` (and potentially outside `BuildDir` too, depending on directory depth). This resulting path is passed to the `cache-archiver`/`cache-extractor` helper commands as `--alternate-file` (see `functions/concrete/run/stages/cache_archive.go` and `cache_extract.go`, and `commands/helpers/cache_archiver.go`/`cache_extractor.go`), which read from or write a `cache.zip` file at that location inside the job's own execution environment.

### Impact Explanation
With `FF_HASH_CACHE_KEYS` enabled, a job can supply `cacheOptions.Key` (the `cache:key` in `.gitlab-ci.yml`, which is expanded through CI/CD variables) such that the alternate cache archive file is written to or read from an arbitrary filesystem path outside the intended cache directory, within the job's own execution context (shell host filesystem for the shell executor, or container filesystem for Docker/Kubernetes executors). This can be used to overwrite arbitrary files reachable by the runner/helper process (shell executor: potentially other projects' shared build/cache directories on a shared host; container executors: files within the job's own container, which is a narrower but still improper containment failure of the intended cache root). This violates the invariant that "file operations must stay within intended build/cache/artifact roots."

### Likelihood Explanation
Preconditions: `FF_HASH_CACHE_KEYS` feature flag must be enabled on the runner/job, and the job must define a `cache:key` (fully attacker-controlled since it is expanded from CI/CD variables under the job author's control). No special privileges beyond authoring a pipeline are needed. This is fully reproducible via a Go unit test on `newCacheConfig` or an end-to-end CI job.

### Recommendation
- In `newCacheConfig`, sanitize (or at minimum path-traverse-check) `humanKey`/`alternateKey` before use in `getArchivePath`, regardless of the `HashCacheKeys` feature-flag state — e.g., always run traversal resolution (like `cachekey.Sanitize` does against a virtual root) on the raw key before it's used to build a filesystem path, independent of whether it is also hashed for the primary key.
- Alternatively/additionally, add a containment check in `getArchivePath` analogous to `cache/cache.go`'s `GetAdapter`, verifying the joined `file` path remains under `build.CacheDir` (e.g., using `filepath.Rel` and rejecting results starting with `..`, or checking `strings.HasPrefix` after joining with a guaranteed trailing separator) before returning it.

### Proof of Concept
Go unit test targeting `newCacheConfig` in `shells/abstract.go` (same package `shells`):

```go
func TestNewCacheConfig_AlternateKeyPathTraversal(t *testing.T) {
    build := &common.Build{
        CacheDir: filepath.FromSlash("/builds/project/cache"),
        BuildDir: filepath.FromSlash("/builds/project"),
        Runner: &common.RunnerConfig{
            RunnerSettings: common.RunnerSettings{
                FeatureFlags: map[string]bool{featureflags.HashCacheKeys: true},
            },
        },
    }
    cfg, _, err := newCacheConfig(build, "../../../outside/cache")
    require.NoError(t, err)

    // AlternateArchiveFile should stay under CacheDir; assert it does NOT
    // resolve outside it.
    resolved := filepath.Join(build.BuildDir, cfg.AlternateArchiveFile)
    assert.True(t, strings.HasPrefix(resolved, build.CacheDir+string(filepath.Separator)),
        "alternate archive file escaped CacheDir: %s", resolved)
}
```

Expected current (buggy) behavior: the assertion fails because `resolved` equals something like `/builds/outside/cache/cache.zip`, outside `build.CacheDir` (`/builds/project/cache`). After the fix, the test should pass because the traversal is neutralized before path construction.

### Citations

**File:** shells/abstract.go (L120-126)
```go
func cacheAlternateKey(humanKey string, hashEnabled bool) string {
	sha256Key := fmt.Sprintf("%x", sha256.Sum256([]byte(humanKey)))
	if hashEnabled {
		return humanKey
	}
	return sha256Key
}
```

**File:** shells/abstract.go (L138-141)
```go
	rawKey := path.Join("/", build.JobInfo.Name, build.GitInfo.Ref)[1:]
	if userKey != "" {
		rawKey = build.GetAllVariables().ExpandValue(userKey)
	}
```

**File:** shells/abstract.go (L143-149)
```go
	hasher := func(s string) string { return s }
	sanitizer := cachekey.Sanitize
	// if hash key support is enabled, we don't need to sanitize keys anymore
	if build.IsFeatureFlagOn(featureflags.HashCacheKeys) {
		hasher = func(s string) string { return fmt.Sprintf("%x", sha256.Sum256([]byte(s))) }
		sanitizer = func(s string) (string, error) { return s, nil }
	}
```

**File:** shells/abstract.go (L173-183)
```go
	getArchivePath := func(key string) (string, error) {
		var err error
		file := path.Join(build.CacheDir, key, "cache.zip")
		if !build.IsFeatureFlagOn(featureflags.UsePowershellPathResolver) {
			file, err = filepath.Rel(build.BuildDir, file)
			if err != nil {
				return "", fmt.Errorf("inability to make the cache file path relative to the build directory (is the build directory absolute?)")
			}
		}
		return file, err
	}
```

**File:** shells/abstract.go (L193-197)
```go
	alternateKey := cacheAlternateKey(humanKey, build.IsFeatureFlagOn(featureflags.HashCacheKeys))
	alternateArchiveFile, err := getArchivePath(alternateKey)
	if err != nil {
		return nil, warning, err
	}
```
