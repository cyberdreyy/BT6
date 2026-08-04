### Title
Path traversal in unsanitized `AlternateKey`/`AlternateFile` allows cross-project cache file rename/exfiltration - ([File: shells/abstract.go], [File: commands/helpers/cache_archiver.go])

### Summary
When `FF_HASH_CACHE_KEYS` is enabled, the "alternate" cache key used to build `--alternate-file` is the raw, job-controlled cache key with no sanitization applied, unlike the primary key path. `CacheArchiverCommand.tryRenameAlternateFile` in `commands/helpers/cache_archiver.go` then unconditionally `os.Rename`s whatever local path that traversal-crafted `AlternateFile` resolves to into the job's own `--file` destination, without validating that the alternate path stays inside the job's cache directory.

### Finding Description
`newCacheConfig` in [1](#0-0)  selects the sanitizer based on `FF_HASH_CACHE_KEYS`:

```go
sanitizer := cachekey.Sanitize
if build.IsFeatureFlagOn(featureflags.HashCacheKeys) {
    hasher = func(s string) string { return fmt.Sprintf("%x", sha256.Sum256([]byte(s))) }
    sanitizer = func(s string) (string, error) { return s, nil }  // no-op
}
```

When the flag is on, `humanKey` is the raw, unsanitized, variable-expanded user cache key [2](#0-1) . `hashedKey` (used for the primary `ArchiveFile`) is a SHA256 hash of that key, so the primary path is safe regardless of traversal characters. However, `cacheAlternateKey` returns the **unsanitized** `humanKey` itself as the alternate key when hashing is enabled:

```go
func cacheAlternateKey(humanKey string, hashEnabled bool) string {
	sha256Key := fmt.Sprintf("%x", sha256.Sum256([]byte(humanKey)))
	if hashEnabled {
		return humanKey   // raw, attacker-controlled, unsanitized
	}
	return sha256Key
}
``` [3](#0-2) 

`AlternateArchiveFile` is then built directly from this raw key via `getArchivePath`, using `path.Join(build.CacheDir, key, "cache.zip")` and (optionally) `filepath.Rel` against `BuildDir` [4](#0-3) . Because `path.Join`/`Clean` resolves `..` lexically, a job-controlled `cache:key` containing enough `../` segments can make `AlternateArchiveFile` resolve to a path outside `CacheDir`, including into the local cache directory of another project sharing the same host cache root (common on shell/docker executors with a shared cache volume).

This attacker-controlled path is passed straight to the cache-archiver helper as `--alternate-file` [5](#0-4) . `tryRenameAlternateFile` performs no validation that `AlternateFile` is confined to the job's own cache directory before calling `os.Rename`:

```go
if _, err := os.Stat(c.AlternateFile); err != nil {
    return
}
if err := os.MkdirAll(filepath.Dir(c.File), 0o700); err != nil { ... }
if err := os.Rename(c.AlternateFile, c.File); err != nil { ... }
``` [6](#0-5) 

It only checks that the primary `c.File` does not already exist — it never checks that `c.AlternateFile` is inside the intended cache root. If a job can predict or brute-force the path of another project's `cache.zip` on the same shared host (cache directory layout is deterministic: `CacheDir/<key>/cache.zip`), it can rename that file into its own build, and the archiver then uploads it to the attacker's own cache URL, all while the source cache entry is destroyed for the victim (rename is a move, not a copy).

### Impact Explanation
Cross-project cache exfiltration and poisoning: an attacker-controlled pipeline can move (steal) another project's local cache archive into its own job when both share a runner host's cache root and `FF_HASH_CACHE_KEYS` is enabled, then upload the stolen content via its own presigned/GoCloud cache URL. It simultaneously deletes the victim's cache file (rename removes the source), which is a poisoning/denial effect on the other project's cache.

### Likelihood Explanation
Requires: (1) `FF_HASH_CACHE_KEYS=true` (an opt-in, transitional feature flag — I could not fully confirm whether it defaults on in this codebase version, `helpers/featureflags/flags.go` was found but not read in full), (2) a shared runner host/cache root serving multiple projects (common with shell or Docker executors using a mounted cache directory), and (3) the attacker being able to predict/guess another project's cache key/path (cache paths are deterministic and derivable from job name/git ref/user-supplied key, not secret). Given these are realistic and common runner deployment conditions, and the cache key is fully job-controlled via `.gitlab-ci.yml` `cache:key` (including CI variable expansion), the bug is reproducible with a crafted pipeline.

### Recommendation
Sanitize/confine the alternate key the same way the primary key is validated — i.e., always run the alternate key through `cachekey.Sanitize` (or an equivalent path-confinement check) regardless of `FF_HASH_CACHE_KEYS`, so `AlternateArchiveFile` can never resolve outside `CacheDir/<project-scoped-root>`. Additionally, harden `tryRenameAlternateFile` in `commands/helpers/cache_archiver.go` to reject any `AlternateFile` that does not resolve (after `filepath.Clean`/`EvalSymlinks`) to a path inside the same parent directory as `c.File`'s cache root, refusing the rename otherwise.

### Proof of Concept
Go unit test targeting `newCacheConfig` (shells/abstract.go) demonstrating the unsanitized alternate path:
```go
func TestNewCacheConfig_AlternateKeyTraversal(t *testing.T) {
    build := &common.Build{
        CacheDir: "/cache",
        BuildDir: "/builds/attacker-project",
        Runner: &common.RunnerConfig{
            RunnerSettings: common.RunnerSettings{
                FeatureFlags: map[string]bool{featureflags.HashCacheKeys: true},
            },
        },
    }
    userKey := "../victim-project/some-cache-key" // attacker-controlled cache:key
    cfg, _, err := newCacheConfig(build, userKey)
    require.NoError(t, err)
    // AlternateArchiveFile should NOT escape the attacker's own cache namespace,
    // but currently resolves into /cache/victim-project/some-cache-key/cache.zip
    assert.NotContains(t, cfg.AlternateArchiveFile, "victim-project",
        "alternate cache path must not resolve into another project's cache directory")
}
```
Complementary test on `tryRenameAlternateFile` (commands/helpers/cache_archiver.go), extending the existing `TestTryRenameAlternateFile` table [7](#0-6) : create `alternateFile` outside the job's own cache dir (e.g. in a sibling "other-project" directory simulating another job's cache), set `AlternateFile` to that path, and assert `os.Rename` is refused (`assert.FileExists(t, alternateFile)`, primary file NOT created) once a containment check is added — currently the test would show the rename succeeding, proving the file is moved cross-directory.

### Citations

**File:** shells/abstract.go (L117-126)
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
```

**File:** shells/abstract.go (L133-150)
```go
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

**File:** shells/abstract.go (L173-204)
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

	cacheConfig := &cacheConfig{
		HumanKey:             humanKey,
		HashedKey:            hashedKey,
		ArchiveFile:          archiveFile,
		AlternateArchiveFile: alternateArchiveFile,
		AlternateKey:         alternateKey,
```

**File:** shells/abstract.go (L1547-1552)
```go
	args := []string{
		"cache-archiver",
		"--file", cacheConfig.ArchiveFile,
		"--alternate-file", cacheConfig.AlternateArchiveFile,
		"--timeout", strconv.Itoa(info.Build.GetCacheRequestTimeout()),
	}
```

**File:** commands/helpers/cache_archiver.go (L268-281)
```go
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
```

**File:** commands/helpers/cache_archiver_test.go (L86-121)
```go
func TestTryRenameAlternateFile(t *testing.T) {
	tests := map[string]struct {
		setupAlternate  bool
		setupPrimary    bool
		noAlternateSet  bool // pass empty string as AlternateFile
		sameAsPrimary   bool // AlternateFile == File
		primaryInSubdir bool // primary lives in a subdirectory that doesn't exist yet
		expectRename    bool
	}{
		"no alternate file set": {
			noAlternateSet: true,
			expectRename:   false,
		},
		"alternate same as primary": {
			sameAsPrimary: true,
			expectRename:  false,
		},
		"primary exists, alternate exists": {
			setupPrimary:   true,
			setupAlternate: true,
			expectRename:   false,
		},
		"primary missing, alternate missing": {
			setupAlternate: false,
			expectRename:   false,
		},
		"primary missing, alternate exists": {
			setupAlternate: true,
			expectRename:   true,
		},
		"primary missing, alternate exists, primary dir missing": {
			setupAlternate:  true,
			primaryInSubdir: true,
			expectRename:    true,
		},
	}
```
