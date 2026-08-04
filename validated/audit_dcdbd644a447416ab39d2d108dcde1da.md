### Title
Post-`Clean` trailing-whitespace trimming in `cachekey.Sanitize` can resurrect an unresolved `..` path-traversal segment - ([File: cache/cachekey/cachekey.go])

### Summary
The function referenced in the question (`Sanitize`) actually lives in `cache/cachekey/cachekey.go`, not `cache/credentials_adapter.go` — that file only contains the `CredentialsAdapter`/factory registry and has no cache-key parsing logic at all [1](#0-0) . The real `Sanitize` implementation trims trailing whitespace from path segments *after* `path.Clean` has already resolved `..`/`.` traversal, which lets an attacker smuggle a segment like `".. "` (dot-dot + trailing space) through `Clean` unmolested and have it collapse into a literal `".."` afterward [2](#0-1) .

### Finding Description
`Sanitize` first normalises the key and calls `path.Clean("/" + normaliser.Replace(cacheKey))` to resolve `..`/`.` traversal against a virtual root [3](#0-2) . Go's `path.Clean` only treats a component as a parent-directory reference if it is *exactly* `".."`; a component such as `".. "` (with a trailing space) is not recognized as `..` and is left untouched by `Clean`.

After `Clean`, the code splits the cleaned string into segments and walks backward, calling `strings.TrimRightFunc(parts[n-1], unicode.IsSpace)` on the last segment(s), stopping as soon as a trimmed segment is non-empty [4](#0-3) . If the attacker supplies a cache key such as `"foo/.. "` (trailing space after the two dots):
1. `normaliser.Replace` does nothing.
2. `path.Clean("/foo/.. ")` does **not** collapse `".. "` because it isn't literally `".."`; the cleaned string remains `"/foo/.. "`.
3. The segment loop trims the trailing space off the last segment, turning `".. "` into `".."` — a literal parent-directory segment that was never subjected to `Clean`'s traversal resolution.
4. The final returned key is `"foo/.."`.

The function's own doc comment implies that traversal resolution happens up-front and only whitespace clean-up happens afterward, but the trimming step can *create* a new, unresolved `..` token after the traversal-resolution pass has already run, defeating the purpose of calling `Clean` before returning the key.

### Impact Explanation
The returned key is used elsewhere (e.g., in `shells/abstract.go` and `functions/concrete/builder/builder.go`) to build cache-key-derived paths and remote cache object keys/URLs. If any downstream consumer relies on the invariant that `Sanitize`'s output can never contain a literal `..` segment — i.e., that it is already "path-traversal safe" — and joins it directly with a base directory or S3 key prefix without a second `Clean`/`filepath.Join` normalization pass, a job-controlled cache key such as `"cache-name/.. "` could cause the resulting path/key to point one level above the intended cache root, potentially colliding with or overwriting another project's/job's cache entry or reading data outside the intended scope. This is a scoped cache-isolation bug matching the "file operations must stay within intended build/cache/artifact roots" invariant.

### Likelihood Explanation
Trivial precondition: any pipeline author can set an arbitrary `cache:key` string (including trailing whitespace) in `.gitlab-ci.yml`. No special privileges are required. The exploit is fully deterministic and reproducible via a unit test.

### Recommendation
Re-run `path.Clean` (or re-validate that no segment equals `.` or `..`) *after* the whitespace-trimming pass, or perform the whitespace trimming per-segment *before* calling `path.Clean`, so that trimming can never re-introduce a traversal token that `Clean` already resolved. Additionally, explicitly reject/strip any resulting segment that equals `.` or `..` post-trim before joining.

### Proof of Concept
```go
func TestSanitize_TrailingSpaceRevivesTraversal(t *testing.T) {
    key, err := cachekey.Sanitize("foo/.. ")
    require.NoError(t, err)
    // BUG: key should never contain a literal ".." segment after sanitization
    assert.NotContains(t, strings.Split(key, "/"), "..",
        "Sanitize must not allow a trailing-space-hidden '..' segment to survive as literal '..'")
}
```
Expected (buggy) result: `key == "foo/.."`, i.e. the assertion fails, proving the traversal segment is resurrected by the post-`Clean` trim step.

### Citations

**File:** cache/credentials_adapter.go (L1-21)
```go
package cache

import (
	"fmt"
	"maps"
	"slices"
	"strings"
	"sync"

	"gitlab.com/gitlab-org/gitlab-runner/cache/cacheconfig"
)

type CredentialsAdapter interface {
	GetCredentials() map[string]string
}

var credentialsFactories = &CredentialsFactoriesMap{}

func CredentialsFactories() *CredentialsFactoriesMap {
	return credentialsFactories
}
```

**File:** cache/cachekey/cachekey.go (L32-35)
```go
	// Decode percent-encoded chars and normalise separators, then
	// resolve traversals against a virtual root so ".." can never
	// escape beyond the root.
	cleaned := path.Clean("/" + normaliser.Replace(cacheKey))
```

**File:** cache/cachekey/cachekey.go (L40-48)
```go
	parts := strings.Split(cleaned[1:], "/")
	n := len(parts)
	for n > 0 {
		parts[n-1] = strings.TrimRightFunc(parts[n-1], unicode.IsSpace)
		if parts[n-1] != "" {
			break
		}
		n--
	}
```
