### Title
Unsanitized alternate cache key allows path traversal into another project's local `cache.zip` when `FF_HASH_CACHE_KEYS` is on - (File: shells/abstract.go)

### Summary
`newCacheConfig` skips `cachekey.Sanitize` entirely when `FF_HASH_CACHE_KEYS` is on, and while the *primary* key is safely SHA-256 hashed, the *alternate* key (`cacheAlternateKey`) returns the raw, unsanitized `humanKey` in that mode. That raw key is fed straight into `getArchivePath` via `path.Join(build.CacheDir, key, "cache.zip")`, so a `cache:key` containing `../` segments produces an `AlternateArchiveFile`/`--alternate-file` path that escapes `build.CacheDir`.

### Finding Description
In `shells/abstract.go`, `newCacheConfig` (lines 133-208):
- `rawKey` comes directly from `build.GetAllVariables().ExpandValue(userKey)` (fully attacker-controlled via the job's `cache:key`, unlike the default job/ref key which is defensively wrapped with `path.Join("/", ...)[1:]`).
- When `featureflags.HashCacheKeys` is on, `sanitizer` is replaced with a no-op (`return s, nil`), so `humanKey = rawKey` unsanitized [1](#0-0) .
- The primary path is safe because `hashedKey = hasher(humanKey)` = `sha256(humanKey)` — a fixed-length hex string with no separators, used for `ArchiveFile` [2](#0-1) .
- However, `alternateKey := cacheAlternateKey(humanKey, hashEnabled)` returns the **unhashed** `humanKey` when `hashEnabled` is true [3](#0-2) , and `AlternateArchiveFile = getArchivePath(alternateKey)` builds `path.Join(build.CacheDir, alternateKey, "cache.zip")` with no traversal check [4](#0-3) .
- `build.CacheDir` is already project-scoped: `b.CacheDir = path.Join(cacheDir, b.ProjectUniqueDir(false))` [5](#0-4) , so path traversal here specifically escapes the *project's* cache root, e.g. into a sibling project's cache directory (or further, host paths, subject to permissions).
- `filepath.Rel(build.BuildDir, file)` is then applied (unless `UsePowershellPathResolver` is set), but `filepath.Rel` does not block or reject traversal — it only re-expresses the same absolute location relative to `BuildDir`; the escaped target is preserved.
- `AlternateArchiveFile` is passed as `--alternate-file` to the `cache-archiver` helper command [6](#0-5) .
- Inside `commands/helpers/cache_archiver.go`, `Execute()` calls `tryRenameAlternateFile()` *before* creating the new archive: if the primary `File` doesn't exist yet but `AlternateFile` does, it performs `os.Rename(c.AlternateFile, c.File)` [7](#0-6) . If the traversal-crafted alternate path resolves to another project's already-materialized `cache.zip` on the shared runner host filesystem, that file gets moved into the attacker's own project's primary cache slot and is then eligible for upload via `uploadExistingArchiveIfNeeded()` (see the `isFileChanged` / upload branch in `Execute`) [8](#0-7) .
- No existing check validates `alternateKey`/`AlternateArchiveFile` for traversal. `cachekey.Sanitize`'s traversal protection (used for `humanKey` and for its own well-tested invariants in `cache/cachekey/cachekey_test.go`) is bypassed entirely by the `HashCacheKeys` no-op sanitizer, and this bypass is not compensated anywhere else for the alternate path.

### Impact Explanation
An unprivileged pipeline author who can set `cache:key` (or a variable it expands to) and who runs on a runner with `FF_HASH_CACHE_KEYS` enabled can craft `AlternateArchiveFile` to point at another project's local `cache.zip` on a shared runner host (or at any file literally named `cache.zip` reachable by relative traversal from `CacheDir`). Via `tryRenameAlternateFile`, that foreign file is moved into their own job's cache slot and can subsequently be re-uploaded as their own project's cache object — resulting in cross-project cache content disclosure/exfiltration (the attacker's project ends up hosting another project's cache archive, which they can download in a later job). This directly violates the "cache keys must not escape `build.CacheDir`" / cross-project isolation invariant.

### Likelihood Explanation
Preconditions: (1) `FF_HASH_CACHE_KEYS` feature flag enabled on the runner, (2) shared/persistent local cache storage on the runner host reused across multiple projects' jobs (e.g., shell/docker executors with a shared cache volume, or concurrent jobs from different projects landing on the same host path), (3) attacker knows or guesses the relative path segments needed to reach a sibling project's cache directory (project unique dirs are derived from predictable identifiers such as namespace/project path). This is fully triggerable by a normal pipeline author simply setting an explicit `cache:key` with `../` sequences — no admin privileges required. Repeatability is high; it is a deterministic string-building bug, not a race condition, and the existing test suite (`TestNewCacheConfig` in `shells/abstract_test.go`) already demonstrates the unsanitized `AlternateArchiveFile` for the "hashed" cases but does not include a `../`-traversal test case, confirming the gap is untested rather than mitigated.

### Recommendation
Sanitize (or otherwise validate against traversal) the alternate key the same way as the primary key, regardless of the `HashCacheKeys` FF state — e.g., always run `cachekey.Sanitize` (or an equivalent traversal-safe check) on `humanKey` before it is used to build `AlternateArchiveFile`, or validate that `getArchivePath`'s result stays within `build.CacheDir` (e.g., via `filepath.Rel(build.CacheDir, absoluteFile)` and rejecting results starting with `..`) before passing it to the archiver/extractor commands.

### Proof of Concept
Go unit test extending `TestNewCacheConfig` in `shells/abstract_test.go`:
```go
"hashed, alternate key escapes CacheDir via traversal": {
    cacheDir: "/some/cache/dir",
    buildDir: "/some/build/dir",
    userKey:  "../../victim-project/some-key",
    ffs: map[string]bool{
        featureflags.HashCacheKeys: true,
    },
    // Expect the test to assert AlternateArchiveFile stays under cacheDir.
    // Actual behavior: AlternateArchiveFile resolves to
    // "../../cache/victim-project/some-key/cache.zip" — outside the
    // per-project CacheDir("/some/cache/dir").
},
```
Assertion: after calling `newCacheConfig(build, "../../victim-project/some-key")`, compute `filepath.Rel(build.CacheDir, absoluteAlternateArchivePath)` and assert it does **not** start with `..` — this assertion should fail with the current code, proving the traversal.

Integration/PoC job idea: on a runner with `FF_HASH_CACHE_KEYS: "true"`, run job A for `project-victim` that creates and archives a cache (leaving `cache.zip` under `CacheDir_A` on the shared runner host). Then run job B for `project-attacker` on the same host with `cache: {key: "../../<victim-unique-dir>/<victim-key>"}`; assert that after `cache-archiver` executes, `project-attacker`'s uploaded cache archive contains `project-victim`'s cached files.

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

**File:** shells/abstract.go (L171-197)
```go
	hashedKey := hasher(humanKey)

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

**File:** shells/abstract.go (L1547-1551)
```go
	args := []string{
		"cache-archiver",
		"--file", cacheConfig.ArchiveFile,
		"--alternate-file", cacheConfig.AlternateArchiveFile,
		"--timeout", strconv.Itoa(info.Build.GetCacheRequestTimeout()),
```

**File:** common/build.go (L511-511)
```go
	b.CacheDir = path.Join(cacheDir, b.ProjectUniqueDir(false))
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

**File:** commands/helpers/cache_archiver.go (L286-322)
```go
func (c *CacheArchiverCommand) Execute(*cli.Context) {
	log.SetRunnerFormatter()

	c.normalizeArgs()
	c.tryRenameAlternateFile()
	if err := validateCacheTransferTuning(c.TransferBufferSize, c.ChunkSize, c.Concurrency); err != nil {
		logrus.Fatalln(err)
	}

	// Enumerate files
	err := c.enumerate()
	if err != nil {
		logrus.Fatalln(err)
	}

	// Skip upload if no files were found
	if len(c.files) == 0 {
		logrus.Warningln("No files to cache.")
		return
	}

	// Check if list of files changed
	if !c.isFileChanged(c.File) {
		if c.AlternateFile != c.File {
			// AlternateFile is set (FF_HASH_CACHE_KEYS compatibility mode): the primary
			// archive may have been downloaded from the alternate URL by the extractor,
			// meaning the primary remote URL does not yet have an object. Upload the
			// existing archive to ensure the primary URL is populated.
			// This handles both transition directions:
			//   FF false→true: primary=hashed, alternate=unhashed
			//   FF true→false: primary=unhashed, alternate=hashed
			c.uploadExistingArchiveIfNeeded()
			return
		}
		logrus.Infoln("Archive is up to date!")
		return
	}
```
