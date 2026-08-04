### Title
GCS-signed cache URLs are never masked in job trace regardless of `objectName` encoding, because `urlsanitizer.tokenParamKeys` has no entries for GCS query parameters - ([File: common/buildlogger/internal/urlsanitizer/urlsanitizer.go])

### Summary
The premise in the question (regex-bypass via crafted `objectName` characters) does not hold, but a related and more serious issue is real: `urlsanitizer.tokenParamKeys` only recognizes AWS/GitLab token parameter names (`private_token`, `authenticity_token`, `rss_token`, `x-amz-signature`, `x-amz-credential`, `x-amz-security-token`) and contains no entries for GCS V2/V4 signed-URL parameters such as `Signature`, `GoogleAccessId`, `X-Goog-Signature`, or `X-Goog-Credential`. Any GCS pre-signed URL that reaches the job trace is therefore never masked, independent of the `objectName`/cache key content — there is no need to "slip past the regex" because the sanitizer simply has no rule for GCS at all.

### Finding Description
`gcsAdapter.presignURL` in `cache/gcs/adapter.go` (and the analogous `cache/gcsv2/adapter.go`) builds a `storage.SignedURLOptions` and calls `storage.SignedURL`/`Bucket.SignedURL`, which produces a URL containing GCS-specific signing parameters (`GoogleAccessId`, `Expires`, `Signature` for V2 signing, or `X-Goog-Algorithm`, `X-Goog-Credential`, `X-Goog-Date`, `X-Goog-Signature` for V4 signing) [1](#0-0) [2](#0-1) .

`common/buildlogger/build_logger.go` applies `urlsanitizer.New` to the trace writer to strip sensitive query parameters before they reach the trace store, but `URLSanitizer.Write` only masks a value when its key (lower-cased) matches an entry in `tokenParamKeys` [3](#0-2) . That map is hard-coded to AWS/GitLab-specific keys and has zero coverage for any GCS parameter name [4](#0-3) .

Consequently, whenever a GCS presigned URL (from `GetDownloadURL`/`GetUploadURL`) is echoed into the job trace by the shell script generator, the `Signature`/`X-Goog-Signature`/`GoogleAccessId` values pass through completely unmasked — this is unconditional and does not depend on any encoding trick in the cache key/`objectName`. I confirmed usage sites of `GetDownloadURL`/`GetUploadURL` exist in `shells/abstract.go` (cache script generation) via grep, but I was not able to read the exact lines to confirm the precise formatting of the emitted curl command in this session; this should be verified directly if further confirmation is required.

### Impact Explanation
If the generated cache curl command (or a verbose/failure-path echo of the download/upload URL) is written to the job trace, the full valid signed URL — including its signature/credential parameters — is exposed in that job's trace for the duration the signed URL remains valid (bounded by the cache adapter's configured timeout). Anyone able to view that job's trace (e.g., other maintainers of the same project, or, on public projects, unauthenticated visitors) can extract a live, still-valid signed URL and use it to read or overwrite the referenced cache object before it expires. This is a violation of the invariant that secrets/tokens must not leak through logs/traces, but note the blast radius is bounded to the same job's cache object and its expiry window, not a broad "another project's" secret unless that job's own trace is itself over-exposed (a separate GitLab access-control question, not a Runner isolation bug).

### Likelihood Explanation
The GCS/GCSv2 cache backends are widely configured and reachable by any user submitting a `.gitlab-ci.yml` with a `cache` stanza; no special privilege is needed to trigger cache upload/download URL generation. The masking gap requires no crafted input at all (it is not conditional on the cache key), so it is deterministic and 100% reproducible whenever a GCS signed URL is written into a trace stream through the sanitizer path — provided that URL actually is echoed into that stream (the part I could not fully confirm from `shells/abstract.go` in this session).

### Recommendation
Add GCS-specific signed-URL query parameter keys (lower-cased: `signature`, `x-goog-signature`, `googleaccessid`, `x-goog-credential`, `x-goog-algorithm`) to `tokenParamKeys` in `common/buildlogger/internal/urlsanitizer/urlsanitizer.go` so GCS presigned URLs receive the same masking treatment as S3 presigned URLs. Additionally, audit `shells/abstract.go`'s cache push/pull script generation to confirm/ensure the full signed URL is never echoed verbatim to trace output (e.g., use `curl -s` without `-v`, or redirect URL construction into a variable that is not printed) as a defense-in-depth measure independent of sanitizer coverage.

### Proof of Concept
```go
func TestURLSanitizer_GCSSignatureNotMasked(t *testing.T) {
    var buf bytes.Buffer
    s := urlsanitizer.New(&nopCloser{&buf})

    gcsURL := "https://storage.googleapis.com/bucket/obj?" +
        "X-Goog-Algorithm=GOOG4-RSA-SHA256&" +
        "X-Goog-Credential=sa%40project.iam.gserviceaccount.com%2F20260804%2Fauto%2Fstorage%2Fgoog4_request&" +
        "X-Goog-Signature=deadbeefcafefeed..."

    _, err := s.Write([]byte(gcsURL))
    require.NoError(t, err)
    require.NoError(t, s.Close())

    // FAILS today: buf.String() still contains the raw X-Goog-Signature value,
    // because "x-goog-signature" is absent from tokenParamKeys.
    assert.NotContains(t, buf.String(), "deadbeefcafefeed",
        "GCS signed URL signature was not masked")
}
```
Expected: the assertion currently fails, proving GCS signed-URL parameters leak into the trace stream unmasked, confirming the gap independent of any `objectName` crafting.

### Citations

**File:** cache/gcs/adapter.go (L82-107)
```go
	suo := storage.SignedURLOptions{
		GoogleAccessID: credentials.AccessID,
		Method:         method,
		Expires:        time.Now().Add(a.timeout),
		ContentType:    contentType,
	}

	if method == http.MethodPut {
		suo.Headers = []string{}
		for key, values := range a.GetUploadHeaders() {
			suo.Headers = append(suo.Headers, fmt.Sprintf("%s:%s", key, strings.Join(values, ";")))
		}
	}

	if credentials.PrivateKey != "" {
		suo.PrivateKey = []byte(credentials.PrivateKey)
	} else {
		logrus.Debug("No private key was provided for GCS cache. Attempting to use instance credentials.")
		suo.SignBytes = a.credentialsResolver.SignBytesFunc(ctx)
	}

	rawURL, err := a.generateSignedURL(a.config.BucketName, a.objectName, &suo)
	if err != nil {
		logrus.Errorf("error while generating GCS pre-signed URL: %v", err)
		return nil
	}
```

**File:** cache/gcsv2/adapter.go (L103-124)
```go
	suo := &storage.SignedURLOptions{
		GoogleAccessID: a.config.AccessID,
		Method:         method,
		Expires:        time.Now().Add(a.timeout),
		ContentType:    contentType,
	}

	if a.config.PrivateKey != "" {
		suo.PrivateKey = []byte(a.config.PrivateKey)
	}

	if method == http.MethodPut {
		suo.Headers = []string{}
		for key, values := range a.GetUploadHeaders() {
			suo.Headers = append(suo.Headers, fmt.Sprintf("%s:%s", key, strings.Join(values, ";")))
		}
	}

	rawURL, err := client.Bucket(a.config.BucketName).SignedURL(a.objectName, suo)
	if err != nil {
		return nil, fmt.Errorf("generating signed URL: %w", err)
	}
```

**File:** common/buildlogger/internal/urlsanitizer/urlsanitizer.go (L14-30)
```go
// tokenParamKeys are the param keys for sensitive tokens we sanitize (replace
// with [MASKED]).
var tokenParamKeys = map[string]struct{}{
	// 20 characters, used for authenticating to GitLab
	"private_token": {},
	// ~88 characters, a base64 encoded string of random 64 bytes
	"authenticity_token": {},
	// 20 characters. RSS feed token. Unlikely to appear in a build log, but here for backwards compatibility.
	"rss_token": {},
	// 64 characters, Amazon presigned signature hex encoded sha256 hmac
	"x-amz-signature": {},
	// Amazon presigned URL credential is always in the format of
	// <access-key>/<date>/<region>/<service>/aws4_request.
	"x-amz-credential": {},
	// Amazon temporary security token from STS.
	"x-amz-security-token": {},
}
```
