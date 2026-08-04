### Title
`cachekey.Sanitize` can be tricked into emitting a literal `".."` path segment via trailing-whitespace unmasking - ([File: cache/cachekey/cachekey.go])

### Summary
`Sanitize` relies on `path.Clean` to collapse `..` traversal tokens before any further processing, but it only recognizes exact `".."` (or `"."`) tokens. An attacker can append trailing whitespace to a `..` segment (e.g. `".. "`) so `path.Clean` treats it as an ordinary literal segment and leaves it untouched; the function's own subsequent trailing-whitespace trimming loop then strips that whitespace, "unmasking" the segment back into a literal `".."` that ends up in the final sanitized key, in direct contradiction to the function's own documented invariant.

### Finding Description
`Sanitize` normalizes separators/encodings and calls `path.Clean("/" + normalised)` at `cache/cachekey/cachekey.go:35`, expecting this to resolve/neutralize all `.`/`..` traversal components within a virtual root. `path.Clean` only special-cases path elements that are *exactly* `"."` or `".."`; a segment like `".. "` (dot-dot-space, or dot-dot-tab/CR/LF) does not match and is passed through unchanged as an opaque path component.

After cleaning, the code splits the result into segments and walks backward from the end, trimming trailing whitespace from the rightmost segments and dropping ones that become empty (`cache/cachekey/cachekey.go:40-48`). This loop stops as soon as a trimmed segment is non-empty. Crucially, it performs the trim *after* `path.Clean` has already run, so a segment that survived `Clean` as `".. "` (because it wasn't an exact `".."` match) gets trimmed to `".."` — now a real traversal token — and is joined straight into the returned key with no re-run of `Clean` or any re-check for `.`/`..`.

Concretely:
- `Sanitize(".. ")` → normalised unchanged → `path.Clean("/.. ")` = `"/.. "` (kept literal since `".. "` ≠ `".."`) → split → `[".. "]` → trimmed to `[".."]` → returned key is exactly `".."`.
- `Sanitize("foo/.. ")` → similarly yields `"foo/.."`.
- Chaining exact `".."` segments before the trailing masked one (e.g. `"a/../.. "`) still collapses the exact ones via `Clean` and leaves exactly one trailing `".. "` to be unmasked, so the maximum achievable escape from this bug is a single literal `".."` as the final path segment of the output.

This directly violates the function's own documented guarantee ("resolves path traversals ... within a virtual root," i.e., `..` can never escape) and is explicitly asserted against in the package's own test, `TestSanitizeInvariants`, which checks `key != ".."` and that no segment equals `".."` [1](#0-0) . The current implementation fails this invariant for whitespace-suffixed traversal segments.

### Impact Explanation
The sanitized key is used as the cache key when constructing cache object storage paths / archive locations (consumed by `functions/concrete/builder/builder.go` and `shells/abstract.go`). Since the bug can only unmask a single trailing `..`, the concrete escape is bounded to one directory level relative to whatever prefix precedes it in the caller (e.g., `<namespace>/<project>/<key>` becomes `<namespace>/<project>` after the final `..` cancels the last real segment, or a bare `".."` result if the whole key is whitespace-masked). This can cause a job's cache key to resolve one level above its intended cache root, which could point at a sibling project/namespace's cache directory depending on how the caller composes the final path/object key — a violation of the "cache/artifact file operations must stay within intended root" invariant, though the depth of escape achievable purely from this bug is limited to one level.

### Likelihood Explanation
Fully attacker-reachable: cache `key:` is a job-controlled field in `.gitlab-ci.yml` (`cache: key:`), and the attacker only needs to end a segment with a `..` immediately followed by any Unicode whitespace character accepted by `unicode.IsSpace` (space, tab, CR, LF, etc.), which YAML strings can contain (e.g. quoted strings or trailing space that CI parsers preserve). No special privileges are required — any pipeline author can set this cache key. The bug is deterministic and easily reproducible.

### Recommendation
Re-run `path.Clean` (or an equivalent traversal check) on the joined key *after* the whitespace-trimming step, or perform trimming before the traversal-cleaning pass and clean again afterward, so no whitespace-masked `.`/`..` token can survive into the final key. Additionally, add an explicit post-trim check that rejects/re-cleans any segment equal to `"."` or `".."` before returning.

### Proof of Concept
Add to `cache/cachekey/cachekey_test.go`:
```go
func TestSanitize_TrailingWhitespaceUnmasksTraversal(t *testing.T) {
    key, err := Sanitize(".. ")
    assert.NoError(t, err)
    assert.NotEqual(t, "..", key, "Sanitize must never return a literal '..' key")

    key2, err2 := Sanitize("foo/.. ")
    assert.NoError(t, err2)
    for _, seg := range strings.Split(key2, "/") {
        assert.NotEqual(t, "..", seg, "no segment must be '..'")
    }
}
```
Running this against the current implementation fails: `Sanitize(".. ")` returns `("..", nil)`, and `Sanitize("foo/.. ")` returns `("foo/..", nil)`, both violating the stated invariant already present in `TestSanitizeInvariants` at `cache/cachekey/cachekey_test.go:132-141`. [2](#0-1) [3](#0-2)

### Citations

**File:** cache/cachekey/cachekey_test.go (L120-145)
```go
func TestSanitizeInvariants(t *testing.T) {
	cases := []string{
		"a", "a/b", "../a", "a/../b", "a/./b",
		"a\\b", `a\..\\b`, "/a/b/", " a ", "...",
		"%2e%2e/%2f", "a/b/c/../../d/e",
	}
	for _, raw := range cases {
		t.Run(raw, func(t *testing.T) {
			key, _ := Sanitize(raw)
			if key == "" {
				return // unsanitisable, nothing to check
			}
			assert.False(t, strings.HasPrefix(key, "/"), "must not start with /")
			assert.False(t, key == ".." || strings.HasPrefix(key, "../"), "must not start with .. segment")
			assert.False(t, strings.Contains(key, `\`), "must not contain backslash")
			assert.False(t, strings.HasSuffix(key, " "), "must not end with space")
			assert.False(t, strings.HasSuffix(key, "/"), "must not end with /")

			// No segment should be "." or ".."
			for _, seg := range strings.Split(key, "/") {
				assert.NotEqual(t, ".", seg, "must not contain '.' segment")
				assert.NotEqual(t, "..", seg, "must not contain '..' segment")
			}
		})
	}
}
```

**File:** cache/cachekey/cachekey.go (L27-57)
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
}
```
