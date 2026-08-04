### Title
Unsanitized cache key flows into `AlternateKey` archive path under `FF_HASH_CACHE_KEYS=on`, allowing path traversal in `cacheArchivePath` - (File: `functions/concrete/run/stages/cache_archive.go`)

### Summary
When the `FF_HASH_CACHE_KEYS` feature flag is enabled, `builder.buildCacheArchive` sets `stages.CacheArchive.AlternateKey` to the raw, expanded, unsanitized cache key (`humanKey`) instead of running it through `cachekey.Sanitize`, while the primary `Key` field is safely hashed via SHA-256. This unsanitized value is passed to `cacheArchivePath(e, key)`, which does `filepath.Join(e.CacheDir, key, "cache.zip")` — a job's `cache:key` (attacker-controlled, expanded from CI variables) containing `../` sequences can therefore steer the "alternate" archive file path outside `CacheDir`.

### Finding Description
In `functions/concrete/builder/builder.go`, `cacheKey()` computes:
- `humanKey`: sanitized via `cachekey.Sanitize` when `HashCacheKeys` FF is off; **raw/unsanitized `rawKey`** when the FF is on.
- `resolvedKey`: equal to `humanKey` when FF is off; SHA-256 hash of `humanKey` when FF is on. [1](#0-0) 

In `buildCacheArchive`, the `AlternateKey` is derived as:
```go
alternateResolved := humanKey
if !b.isFeatureFlagOn(featureflags.HashCacheKeys) {
    alternateResolved = fmt.Sprintf("%x", sha256.Sum256([]byte(humanKey)))
}
``` [2](#0-1) 

- When FF is **off**: `alternateResolved` = SHA-256(humanKey) — safe hex string, no traversal risk.
- When FF is **on**: `alternateResolved` = `humanKey`, which under this FF equals the *raw, unsanitized* expanded `cache:key` value — never passed through `cachekey.Sanitize`.

This `AlternateKey` flows into `stages.CacheArchive.AlternateKey` and is used in:
```go
func (s CacheArchive) alternateArchivePath(e *env.Env) string {
    return cacheArchivePath(e, s.AlternateKey)
}
func cacheArchivePath(e *env.Env, key string) string {
    absPath := filepath.Join(e.CacheDir, key, "cache.zip")
    ...
}
``` [3](#0-2) 

The archive run logic adds `--alternate-file` whenever `AlternateKey != "" && AlternateKey != Key`:
```go
if s.AlternateKey != "" && s.AlternateKey != s.Key {
    args = append(args, "--alternate-file", s.alternateArchivePath(e))
}
``` [4](#0-3) 

Since `Key` is a SHA-256 hex digest and `AlternateKey` is the raw human key, they will essentially always differ, so `--alternate-file` is populated on virtually every cache-enabled job when this FF is on. `filepath.Join` cleans `..` sequences algebraically (it does not block traversal — it resolves it), so a `cache:key` value like `../../../../tmp/evil` (after CI variable expansion, which is fully attacker-controlled via job YAML or pipeline variables) produces an `AlternateKey` that, once joined with `CacheDir`, resolves to a path outside the intended cache directory. The `--file`/primary path is safe (hashed), but the `--alternate-file` path is not.

The existing protective mechanism (`cachekey.Sanitize`, which explicitly resolves `.`/`..` against a virtual root so they can never escape) is only invoked in the FF-off branch of `cacheKey()`; the FF-on branch for `AlternateKey` bypasses it entirely.

### Impact Explanation
`cacheArchivePath` is used to build the `--file`/`--alternate-file` argument passed to the `cache-archiver` helper subprocess, which writes the job's cache zip to that path. A traversal-controlled `AlternateKey` lets an attacker-controlled path escape `CacheDir`, potentially writing (or later, on extraction/rename, causing) file writes outside the intended cache root within the filesystem accessible to the runner/build process. The severity is bounded by the runner process's own filesystem permissions (this is not a container escape or privilege escalation), but it is a within-host arbitrary-file-write/overwrite via a job field that is otherwise supposed to be confined to the cache directory tree — a real path-confinement violation matching the "file operations must stay within intended... cache... roots" invariant.

### Likelihood Explanation
Preconditions: `FF_HASH_CACHE_KEYS` must be enabled (this is a feature flag setting, not attacker controlled) and the job must define a `cache:key` (or its default derived from job name/ref, though the default is not attacker-arbitrary). If the flag is enabled by an operator, any unprivileged pipeline author can set `cache:key: "../../../whatever"` (or via a CI/CD variable used in `cache:key: $MY_KEY`) to control `humanKey`/`AlternateKey` directly, since `b.variables.ExpandValue(name)` expands the raw configured key with no path sanitation in this branch. This is straightforward and repeatable — every job run with such a key reaches the vulnerable path.

### Recommendation
Sanitize `humanKey` with `cachekey.Sanitize` (or otherwise strip `../` traversal) before using it as `AlternateKey` in the `FF_HASH_CACHE_KEYS` branch of `buildCacheArchive` (and the equivalent alternate-key derivation in `buildCacheSources`/`buildCacheExtract`, which has the same `alternateResolved := primaryHuman` pattern). Alternatively, harden `cacheArchivePath` itself to reject/clamp any resulting path that escapes `e.CacheDir` (e.g., verify with `filepath.Rel` that the result has no `..` prefix), defensively closing this class of bug regardless of which caller passes an unsanitized key.

### Proof of Concept
Go unit test in `functions/concrete/builder/builder_test.go` style:
```go
func TestBuildCacheArchive_AlternateKeyTraversal(t *testing.T) {
    // Enable HashCacheKeys FF
    // Configure job with Cache: []spec.Cache{{Key: "../../../../tmp/evil", Paths: []string{"foo"}}}
    cfg, err := Build(job, vars, WithFeatureFlag(func(f string) bool {
        return f == featureflags.HashCacheKeys
    }))
    require.NoError(t, err)

    var parsed run.Config
    require.NoError(t, json.Unmarshal(cfg, &parsed))
    archive := parsed.CacheArchive[0]

    // Key must be a 64-char hex SHA-256 digest (safe)
    assert.Regexp(t, `^[0-9a-f]{64}$`, archive.Key)

    // BUG: AlternateKey still contains raw traversal sequence, unsanitized
    assert.Contains(t, archive.AlternateKey, "..")
}
```
Complementary assertion at the `cacheArchivePath` level (in `functions/concrete/run/stages/cache_archive_test.go`):
```go
func TestCacheArchivePath_TraversalEscapesCacheDir(t *testing.T) {
    e := &env.Env{CacheDir: "/builds/cache", WorkingDir: "/builds/project"}
    path := cacheArchivePath(e, "../../../../tmp/evil")
    // Expect failure: path should stay under CacheDir but does not
    assert.True(t, strings.HasPrefix(filepath.Clean(filepath.Join(e.CacheDir, "../../../../tmp/evil")), e.CacheDir),
        "resulting path escaped CacheDir: %s", path)
}
```
Both assertions should fail against current code, confirming the traversal is not blocked when `AlternateKey` carries the raw key.

### Citations

**File:** functions/concrete/builder/builder.go (L361-375)
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

		archive := stages.CacheArchive{
			Name:                   humanKey,
			Key:                    resolvedKey,
			AlternateKey:           alternateResolved,
```

**File:** functions/concrete/builder/builder.go (L578-602)
```go
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

	if humanKey == "" {
		return "", "", warnings, fmt.Errorf("empty cache key")
	}

	resolvedKey := humanKey
	if b.isFeatureFlagOn(featureflags.HashCacheKeys) {
		resolvedKey = fmt.Sprintf("%x", sha256.Sum256([]byte(humanKey)))
	}

	return humanKey, resolvedKey, warnings, nil
```

**File:** functions/concrete/run/stages/cache_archive.go (L53-55)
```go
	if s.AlternateKey != "" && s.AlternateKey != s.Key {
		args = append(args, "--alternate-file", s.alternateArchivePath(e))
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
