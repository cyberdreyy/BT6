### Title
Path traversal via unsanitized `AlternateKey`/`AlternateArchiveFile` when `FF_HASH_CACHE_KEYS` is enabled - ([File: functions/concrete/builder/builder.go], [File: shells/abstract.go], [File: commands/helpers/cache_archiver.go])

### Summary
When `FF_HASH_CACHE_KEYS` is enabled, the *primary* cache key is safely hashed with SHA-256 before being used to build a filesystem path, but the *alternate* (migration-compatibility) key is set to the raw, **unsanitized** `humanKey` and is later joined directly into a filesystem path with no traversal check. An attacker who controls the `cache.key` field of a job (e.g. via `.gitlab-ci.yml` or a variable it expands) can inject `../` sequences that escape `build.CacheDir` when the runner attempts to rename/read the "alternate" local cache file.

### Finding Description
`cacheKey()` in `functions/concrete/builder/builder.go` computes `humanKey` from either a virtual-root-normalized default (`path.Join("/", JobInfo.Name, GitInfo.Ref)[1:]`, which is safe because `path.Join` resolves `..` against `/`) or, if the job sets `cache.key`, from `b.variables.ExpandValue(name)` directly: [1](#0-0) 

When `FF_HASH_CACHE_KEYS` is on, `humanKey = rawKey` with **no call to `cachekey.Sanitize`**, so any `../` in the user-supplied key survives verbatim. The primary `resolvedKey` is safely hashed: [2](#0-1) 

However, in `buildCacheArchive`, the *alternate* key used for FF-toggle migration is set to the raw `humanKey` itself (not hashed) precisely when the FF is on: [3](#0-2) 

The same pattern exists in the non-BuildConfig code path, `newCacheConfig` in `shells/abstract.go`: when `HashCacheKeys` is on, `sanitizer` becomes a no-op identity function, and `cacheAlternateKey()` returns the raw `humanKey` (not its hash) as the alternate key: [4](#0-3) 

This unsanitized `alternateKey`/`AlternateKey` is joined straight into the cache directory path with no re-application of `path.Join("/", ...)` virtual-root normalization: [5](#0-4) [6](#0-5) 

That path is passed to the `cache-archiver` helper as `--alternate-file`, which performs an `os.Rename(c.AlternateFile, c.File)` operation once it confirms the file exists: [7](#0-6) 

Because `os.Rename`/`filepath.Join`/`filepath.Rel` do not prevent `..` from escaping `CacheDir`, a `cache.key` value such as `../../../../tmp/evil` (or one that expands from a CI/CD variable to that value) causes `AlternateFile` to resolve outside `build.CacheDir`. The existing `cachekey.Sanitize` mechanism — which is specifically designed to resolve traversal within a virtual root — is bypassed entirely for this path because it's only invoked when the FF is *off*, and even then only for the primary key, never for constructing the alternate path safely from an already-unsanitized value.

### Impact Explanation
This allows a job with `FF_HASH_CACHE_KEYS=true` and an attacker-controlled `cache.key` to make the runner check for and `os.Rename` a file from an attacker-chosen path outside `CacheDir` into the primary cache location (or, on the extract-fallback direction, could plausibly influence which local file is treated as an existing cache candidate). This is a scoped file-system path-traversal within the runner's own local cache-management operations — it does not directly leak the target file's content to the attacker's job output automatically, but it does let job configuration steer local filesystem rename operations outside the intended cache root, which is a break of the "file operations must stay within intended build/cache/artifact roots" invariant.

### Likelihood Explanation
Fully feasible for any pipeline author (unprivileged attacker per the threat model) who can set a `cache: key:` value (including via forked-pipeline CI variables) and enable/rely on `FF_HASH_CACHE_KEYS=true` (which is a per-runner/per-job feature flag, documented as controllable via job or runner config). No special privileges beyond authoring a `.gitlab-ci.yml` are required; the flaw is deterministic and repeatable across runs.

### Recommendation
Sanitize/normalize the key used to build the *alternate* path exactly as is done for the primary path, regardless of `FF_HASH_CACHE_KEYS` state. Concretely: in `builder.go`'s `buildCacheArchive`/`buildCacheSources` and in `shells/abstract.go`'s `cacheAlternateKey`, always compute the on-disk/alternate path from a value that has passed through `cachekey.Sanitize` (or an equivalent virtual-root `path.Join("/", key)` normalization) before joining with `CacheDir`, even when the FF is on and the primary/human key is intentionally left unsanitized for display/hashing purposes. The unsanitized `humanKey` may still be used as input to hashing/display, but any code path that turns a cache key into a literal filesystem path segment must sanitize it first.

### Proof of Concept
Go unit test extending `TestBuild_FeatureFlags`/`TestNewCacheConfig` style tests:
```go
func TestCacheArchive_AlternateKeyTraversal(t *testing.T) {
    job := baseJob()
    job.Cache = []spec.Cache{
        {Key: "../../../../tmp/evil", Paths: []string{"build/"}, Policy: spec.CachePolicyPullPush},
    }
    vars := newTestVars(t, nil, expandValues(map[string]string{"../../../../tmp/evil": "../../../../tmp/evil"}))
    ff := func(name string) bool { return name == featureflags.HashCacheKeys }
    config := buildConfig(t, job, vars, WithFeatureFlagProvider(ff))

    require.Len(t, config.CacheArchive, 1)
    alt := config.CacheArchive[0].AlternateKey
    // Assert the alternate key/path does not contain traversal segments
    // and resolves within CacheDir once joined.
    assert.NotContains(t, alt, "..")
}
```
Expected current (buggy) behavior: `alt` equals the raw string `"../../../../tmp/evil"`, and once joined via `filepath.Join(CacheDir, alt, "cache.zip")` and passed to `cache-archiver --alternate-file`, the resulting path resolves outside `CacheDir`. Expected fixed behavior: `alt` should be sanitized (e.g., `"tmp/evil"` per `cachekey.Sanitize` semantics) so the joined path always stays under `CacheDir`.

### Citations

**File:** functions/concrete/builder/builder.go (L361-371)
```go
		// AlternateKey is the FF-opposite local cache path so
		// cache-archiver can rename the previous-FF archive into the
		// current-FF path (commands/helpers/cache_archiver.go:251).
		alternateResolved := humanKey
		if !b.isFeatureFlagOn(featureflags.HashCacheKeys) {
			alternateResolved = fmt.Sprintf("%x", sha256.Sum256([]byte(humanKey)))
		}
		if alternateResolved == resolvedKey {
			alternateResolved = ""
		}

```

**File:** functions/concrete/builder/builder.go (L570-591)
```go
func (b *builder) cacheKey(name string) (string, string, []string, error) {
	// Virtual-root prefix prevents a leading / or .. in JobName/Ref from producing an unexpected key,
	// especially under FF_HASH_CACHE_KEYS=on where humanKey is used unsanitized.
	rawKey := path.Join("/", b.meta.JobInfo.Name, b.meta.GitInfo.Ref)[1:]
	if name != "" {
		rawKey = b.variables.ExpandValue(name)
	}

	var warnings []string
	var humanKey string
	if b.isFeatureFlagOn(featureflags.HashCacheKeys) {
		humanKey = rawKey
	} else {
		sanitized, err := cachekey.Sanitize(rawKey)
		switch {
		case err != nil:
			warnings = append(warnings, err.Error())
		case sanitized != rawKey:
			warnings = append(warnings, fmt.Sprintf("cache key %q sanitized to %q", rawKey, sanitized))
		}
		humanKey = sanitized
	}
```

**File:** functions/concrete/builder/builder.go (L597-602)
```go
	resolvedKey := humanKey
	if b.isFeatureFlagOn(featureflags.HashCacheKeys) {
		resolvedKey = fmt.Sprintf("%x", sha256.Sum256([]byte(humanKey)))
	}

	return humanKey, resolvedKey, warnings, nil
```

**File:** shells/abstract.go (L117-149)
```go
// cacheAlternateKey returns the "other" archive key relative to the current FF_HASH_CACHE_KEYS setting.
// When hashing is enabled, the alternate is the unhashed (human-readable) key.
// When hashing is disabled, the alternate is the SHA256-hashed key.
func cacheAlternateKey(humanKey string, hashEnabled bool) string {
	sha256Key := fmt.Sprintf("%x", sha256.Sum256([]byte(humanKey)))
	if hashEnabled {
		return humanKey
	}
	return sha256Key
}

// newCacheConfig creates a cacheConfig for a provided build and userKey.
// If the userKey is empty, it is defaulted to `${jobName}/${gitRef}`.
// Based on the build configuration (ie. FFs), the cacheConfig provides either a sanitized/human-readable cache
// key, or raw/hashed cache key.
// Additionally, keyChecks can be provided, which validate cache keys just after sanitation.
func newCacheConfig(build *common.Build, userKey string, keyChecks ...func(string) bool) (*cacheConfig, string, error) {
	if build.CacheDir == "" {
		return nil, "", fmt.Errorf("unset cache directory")
	}

	rawKey := path.Join("/", build.JobInfo.Name, build.GitInfo.Ref)[1:]
	if userKey != "" {
		rawKey = build.GetAllVariables().ExpandValue(userKey)
	}

	hasher := func(s string) string { return s }
	sanitizer := cachekey.Sanitize
	// if hash key support is enabled, we don't need to sanitize keys anymore
	if build.IsFeatureFlagOn(featureflags.HashCacheKeys) {
		hasher = func(s string) string { return fmt.Sprintf("%x", sha256.Sum256([]byte(s))) }
		sanitizer = func(s string) (string, error) { return s, nil }
	}
```

**File:** shells/abstract.go (L173-197)
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

	archiveFile, err := getArchivePath(hashedKey)
	if err != nil {
		return nil, warning, err
	}

	// alternateKey is always the "other" naming scheme relative to the current FF setting:
	// - FF ON:  primary=hashed, alternate=unhashed → enables upgrade of old unhashed artifacts
	// - FF OFF: primary=unhashed, alternate=hashed → enables downgrade of old hashed artifacts
	alternateKey := cacheAlternateKey(humanKey, build.IsFeatureFlagOn(featureflags.HashCacheKeys))
	alternateArchiveFile, err := getArchivePath(alternateKey)
	if err != nil {
		return nil, warning, err
	}
```

**File:** functions/concrete/run/stages/cache_archive.go (L110-121)
```go
func (s CacheArchive) alternateArchivePath(e *env.Env) string {
	return cacheArchivePath(e, s.AlternateKey)
}

func cacheArchivePath(e *env.Env, key string) string {
	absPath := filepath.Join(e.CacheDir, key, "cache.zip")
	rel, err := filepath.Rel(e.WorkingDir, absPath)
	if err != nil {
		return absPath
	}
	return rel
}
```

**File:** commands/helpers/cache_archiver.go (L253-284)
```go
func (c *CacheArchiverCommand) tryRenameAlternateFile() {
	if c.AlternateFile == "" || c.AlternateFile == c.File {
		return
	}

	_, err := os.Stat(c.File)
	if err == nil {
		logrus.Debugln("Primary cache file already exists locally, skipping rename from alternate")
		return
	}
	if !errors.Is(err, fs.ErrNotExist) {
		logrus.WithError(err).Warningln("Failed to stat primary cache file")
		return
	}

	if _, err := os.Stat(c.AlternateFile); err != nil {
		logrus.Debugln("Alternate cache file not found locally, nothing to rename")
		return
	}

	if err := os.MkdirAll(filepath.Dir(c.File), 0o700); err != nil {
		logrus.WithError(err).Warningln("Failed to create directory for cache file rename")
		return
	}

	if err := os.Rename(c.AlternateFile, c.File); err != nil {
		logrus.WithError(err).Warningln("Failed to rename alternate cache file to primary")
		return
	}

	logrus.Infoln("Renamed alternate cache file to primary")
}
```
