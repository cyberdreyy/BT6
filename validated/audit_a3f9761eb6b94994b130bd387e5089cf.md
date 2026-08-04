Confirmed: `cachekey.Sanitize` only normalizes path separators/traversal (`\`, `%2f`, `..`, whitespace) and does not strip or reject IAM-glob metacharacters (`*`, `?`). These pass straight through into `objectName` and then into `generateSessionPolicy`'s `Resource` field via `fmt.Sprintf("arn:%s:s3:::%s/%s", ...)`.### Title
IAM session policy `Resource` built from unescaped cache key allows wildcard (`*`/`?`) expansion of AssumeRole scope beyond the job's own object path - (File: cache/s3v2/s3.go)

### Summary
`generateSessionPolicy` builds the `Resource` ARN by directly interpolating the caller-controlled `objectName` (which is derived from the pipeline-controlled `cache:key`) into an IAM policy string via `fmt.Sprintf("arn:%s:s3:::%s/%s", ...)`. Because `cachekey.Sanitize` only normalizes path separators and traversal sequences (`\`, `%2f`, `..`, trailing whitespace) and does not strip or reject IAM glob metacharacters (`*`, `?`), a pipeline author can embed these characters in `cache:key` so that the resulting `Resource` string is interpreted by AWS IAM as a wildcard pattern rather than a literal object key, widening the AssumeRole session policy beyond the job's own cache namespace.

### Finding Description
The call chain is: `s3Adapter.GetGoCloudURL` → `FetchCredentialsForRole` (`cache/s3v2/s3.go:312`) → `generateSessionPolicy(bucketName, objectName, upload)` (`cache/s3v2/s3.go:229-274`) → `sts.AssumeRole` with `Policy: aws.String(sessionPolicy)` (`cache/s3v2/s3.go:364-369`).

`objectName` originates from the job's `cache:key`, which flows through `GetAdapter` in `cache/cache.go:27-81` (building `runner/<token>/project/<id>/<key>`) and through `cachekey.Sanitize` in `cache/cachekey/cachekey.go:27-57`. That sanitizer only:
- decodes `%2f`/`%2e`,
- converts `\` to `/`,
- resolves `.`/`..` path traversal within a virtual root,
- trims trailing whitespace.

It performs no validation or escaping of the characters `*` or `?`. These characters have no special meaning to Go's `path.Clean`/`strings` handling, so `Sanitize` passes them through unchanged, and `GetAdapter`'s prefix check (`strings.HasPrefix(fullPath, basePath+"/")`) only guards against path traversal escaping the project namespace — it does not reject glob characters within the final key segment.

In `generateSessionPolicy`, the code comment states that using `encoding/json` (`json.Marshal(doc)`) prevents "special characters in objectName ... from alter[ing] the policy structure" — this is true for JSON syntax characters (quotes, backslashes, control characters), because JSON marshalling escapes them at the string-value level. However, `*` and `?` are not JSON metacharacters; they are AWS IAM policy `Resource`-matching metacharacters, evaluated by AWS after the JSON has been parsed. JSON escaping does nothing to neutralize IAM's own wildcard semantics. So a `cache:key` such as `foo/*` or `*` produces a `Resource` value like `arn:aws:s3:::bucket/runner/<token>/project/<id>/foo/*`, which IAM's `AssumeRole` `Policy` parameter honors as a prefix/glob match over any S3 key sharing that prefix — potentially including sibling cache keys, other jobs' keys, or (if the wildcard is placed near the project/runner path level) other projects' objects, bounded only by the underlying IAM role's own permission boundary (a stated precondition of this bug).

### Impact Explanation
If the underlying role (`RoleARN`) has permissions over more than just the exact object key (e.g., broad `s3:GetObject`/`s3:PutObject` on `bucket/*` scoped to STS-limited AssumeRole calls, which is the typical multi-tenant runner deployment pattern), a job can widen its session policy beyond its own `runner/<token>/project/<id>/<cacheKey>` path. This breaks the stated invariant that "a job's session policy Resource must resolve to exactly its own cache object path, never a wildcard superset," and can enable cross-project/cross-job cache read or write within the bounds of the role's broader access.

### Likelihood Explanation
This is straightforwardly reachable by any pipeline author who can set `cache:key` in `.gitlab-ci.yml` (a normal, unprivileged CI configuration field). It requires RoleARN-based S3 cache auth (a supported, documented configuration: `S3.RoleARN`/`S3.UploadRoleARN`), and requires the underlying IAM role to have access to sibling objects — which is a realistic operator setup for shared/multi-project runners using a single scoping role plus per-job AssumeRole session policies as the sole additional restriction. No additional privilege is needed to trigger it; the attack is a single job run with a crafted cache key.

### Recommendation
Reject or escape IAM policy wildcard characters (`*`, `?`) in the cache-key-derived `objectName` before constructing the session policy `Resource`, e.g.:
- In `cache/cachekey/cachekey.go` `Sanitize`, reject/replace `*` and `?` (and any other IAM `Resource`-matching special characters) so cache keys cannot contain them, or
- In `cache/s3v2/s3.go` `generateSessionPolicy`, explicitly escape `*` → `${*}` region-agnostic literal-escaping is not supported by IAM Resource ARNs, so the correct fix is to reject keys containing `*`/`?` (return an error from `generateSessionPolicy`/`FetchCredentialsForRole`) rather than silently passing them into the `Resource` field.
- Add a defense-in-depth check right before `sts.AssumeRole` that fails the request if `objectName` contains `*` or `?`.

### Proof of Concept
```go
// cache/s3v2/s3_wildcard_test.go
func TestGenerateSessionPolicy_RejectsGlobChars(t *testing.T) {
    c := &s3Client{awsConfig: &aws.Config{Region: "us-east-1"}, s3Config: &cacheconfig.CacheS3Config{}}

    // Attacker-controlled cache key containing IAM wildcard chars, as would
    // survive cachekey.Sanitize and GetAdapter's basePath prefix check.
    objectName := "runner/shorttoken/project/10/foo/*"

    policy, err := c.generateSessionPolicy("test-bucket", objectName, false)
    require.NoError(t, err)

    var doc policyDocument
    require.NoError(t, json.Unmarshal([]byte(policy), &doc))
    resource := doc.Statement[0].Resource

    // Assert: the Resource must be a literal path with no glob metacharacters,
    // matching exactly the caller's own object — this assertion currently FAILS,
    // demonstrating the bug.
    assert.NotContains(t, resource, "*", "Resource must not contain IAM wildcard '*'")
    assert.NotContains(t, resource, "?", "Resource must not contain IAM wildcard '?'")
    assert.Equal(t, "arn:aws:s3:::test-bucket/runner/shorttoken/project/10/foo/*", resource) // shows literal pass-through
}
```
An integration-level PoC would run `AssumeRole` against a test STS/S3 endpoint (e.g. via `gofakes3` + a policy-simulation mock) with `cache:key: "*"` and verify the returned session credentials can `GetObject`/`PutObject` on a sibling project's key under the same bucket, proving the cross-project impact concretely.