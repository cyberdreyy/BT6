### Title
Path traversal via unsanitized `AlternateKey` when `FF_HASH_CACHE_KEYS` is enabled - ([File: functions/concrete/builder/builder.go], [File: functions/concrete/run/stages/cache_archive.go])

### Summary
When `FF_HASH_CACHE_KEYS` is enabled, the primary cache archive key (`resolvedKey`) is a sha256 hash of the human key and is therefore safe, but the `AlternateKey` computed for archive migration is set to the raw, unsanitized `humanKey` (i.e. the raw `cache:key` value after variable expansion). This unsanitized value flows into `cacheArchivePath`'s `filepath.Join(e.CacheDir, key, "cache.zip")` with no traversal checks, allowing a pipeline author to control the resulting archive path via `../` sequences in `cache:key`.

### Finding Description
In `functions/concrete/builder/builder.go`, `cacheKey()` computes:
- `humanKey`: the raw, variable-expanded `cache:key` value when `HashCacheKeys` FF is ON, or `cachekey.Sanitize(rawKey)` when OFF.
- `resolvedKey`: `sha256(humanKey)` when FF is ON, or `humanKey` (already sanitized) when OFF. [1](#0-0) 

In `buildCacheArchive()`, the alternate key is derived from `humanKey`, not from the sanitized/hashed `resolvedKey`:
```go
alternateResolved := humanKey
if !b.isFeatureFlagOn(featureflags.HashCacheKeys) {
    alternateResolved = fmt.Sprintf("%x", sha256.Sum256([]byte(humanKey)))
}
``` [2](#0-1) 

- When the FF is OFF: `humanKey` is already run through `cachekey.Sanitize`, which resolves `..`/`.` inside a virtual root and cannot escape it, and `alternateResolved` further hashes that sanitized value — both safe. [3](#0-2) 
- When the FF is ON: `humanKey = rawKey` (the raw, attacker/user-controlled, variable-expanded cache key, no `Sanitize` call at all), and this raw value is used directly as `alternateResolved` → `stages.CacheArchive.AlternateKey`. The primary `Key` (`resolvedKey`) is a sha256 hex hash and is safe, but `AlternateKey` is not.

This `AlternateKey` is passed unmodified into `CacheArchive.Run`, which calls `alternateArchivePath(e)` → `cacheArchivePath(e, s.AlternateKey)`:
```go
func cacheArchivePath(e *env.Env, key string) string {
    absPath := filepath.Join(e.CacheDir, key, "cache.zip")
    rel, err := filepath.Rel(e.WorkingDir, absPath)
    ...
}
``` [4](#0-3) 

`filepath.Join` cleans the path but does not prevent the result from escaping `e.CacheDir` if `key` contains enough `../` segments; `filepath.Rel` against `e.WorkingDir` provides no additional confinement — it can legitimately yield a relative path with leading `../` components that still points outside both `CacheDir` and `WorkingDir`. The computed path is passed straight to `cache-archiver` as `--alternate-file <path>` [5](#0-4) , causing the helper to write the cache zip to an attacker-chosen filesystem location relative to the job's working directory/cache dir tree.

### Impact Explanation
A pipeline author controls `cache:key` (a normal `.gitlab-ci.yml` field, further expandable via CI variables), which becomes `rawKey`/`humanKey` with no traversal sanitization applied when `FF_HASH_CACHE_KEYS` is on. This lets the job force the "alternate" cache archive file to be written outside the intended `CacheDir`, effectively an attacker-controlled file write path within the executor's filesystem — while the "primary" key path remains hardened (a hash), masking the issue if only the primary path is reviewed/tested. This matches the scoped impact: the less-scrutinized alternate-key path doubles the attack surface even though the primary path is safe.

### Likelihood Explanation
Preconditions are realistic and attacker-reachable without any privileged/admin action: only `FF_HASH_CACHE_KEYS=true` needs to be set (a documented, user/operator-settable feature flag), and the job author supplies a `cache:key` value containing `../` sequences (directly or via a CI variable they control, e.g. `key: $CI_COMMIT_REF_NAME` combined with a branch name containing `../`, or a literal key string). Every job run with a non-trivial `cache:paths`/`untracked` will trigger `buildCacheArchive`, making this deterministically reproducible whenever the FF is enabled.

### Recommendation
Apply the same `cachekey.Sanitize` (or an equivalent traversal-safe normalization) to `humanKey`/`rawKey` unconditionally, regardless of `FF_HASH_CACHE_KEYS` state, before it is used to build `AlternateKey` in `buildCacheArchive` (and the analogous alternate-key logic in `buildCacheSources`). Alternatively, have `cacheArchivePath` validate that the resulting absolute path is still lexically contained within `e.CacheDir` (e.g. via `filepath.Clean` + prefix check or `filepath.Rel` failure detection) and reject/replace the key if not.

### Proof of Concept
Go unit test in `functions/concrete/builder` (extending `builder_test.go`):
1. Configure `isFeatureFlagOn` to report `featureflags.HashCacheKeys` as `true`.
2. Set `cache.Key` (job spec) to `"../../../../tmp/evil"`.
3. Call `buildCacheArchive()` and assert:
   - `archive.Key` is a 64-char hex sha256 string (safe).
   - `archive.AlternateKey == "../../../../tmp/evil"` (unsanitized, confirming the bug).
4. Follow-up integration/fuzz test in `functions/concrete/run/stages/cache_archive_test.go`: call `cacheArchivePath(e, alternateKey)` with the same traversal string and assert the resulting path lies within `e.CacheDir` (`strings.HasPrefix(filepath.Clean(absPath), filepath.Clean(e.CacheDir))`) — the test should fail on current code, proving traversal escapes the cache root.

### Citations

**File:** functions/concrete/builder/builder.go (L361-370)
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

**File:** cache/cachekey/cachekey.go (L27-56)
```go
func Sanitize(cacheKey string) (string, error) {
	if cacheKey == "" {
		return "", nil
	}

	// Decode percent-encoded chars and normalise separators, then
	// resolve traversals against a virtual root so ".." can never
	// escape beyond the root.
	cleaned := path.Clean("/" + normaliser.Replace(cacheKey))

	// Strip the leading "/" we added, split into segments, then walk
	// backwards trimming trailing whitespace from the rightmost
	// segments—dropping any that become empty.
	parts := strings.Split(cleaned[1:], "/")
	n := len(parts)
	for n > 0 {
		parts[n-1] = strings.TrimRightFunc(parts[n-1], unicode.IsSpace)
		if parts[n-1] != "" {
			break
		}
		n--
	}

	key := strings.Join(parts[:n], "/")

	if key == "" {
		return "", fmt.Errorf("cache key %q could not be sanitized", cacheKey)
	}

	return key, nil
```

**File:** functions/concrete/run/stages/cache_archive.go (L53-55)
```go
	if s.AlternateKey != "" && s.AlternateKey != s.Key {
		args = append(args, "--alternate-file", s.alternateArchivePath(e))
	}
```

**File:** functions/concrete/run/stages/cache_archive.go (L105-121)
```go
// archivePath mirrors CacheExtract.archivePath — see that doc comment.
func (s CacheArchive) archivePath(e *env.Env) string {
	return cacheArchivePath(e, s.Key)
}

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
