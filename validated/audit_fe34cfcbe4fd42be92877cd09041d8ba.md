### Title
Unmasked presigned cache URL (with signature) leaks into job trace via unwrapped `*url.Error` in `getCache`/`doRetry`/`warningln` - (File: `commands/helpers/cache_extractor.go`)

### Summary
`getCache` wraps the raw error from `http.Client.Get(rawURL)` in `retryableErr` without ever passing it through `url_helpers.CleanURL`, and this error is logged verbatim both on each retry (`retry_helper.go`) and at final failure (`warningln` in `Execute`). Since Go's `*url.Error.Error()` embeds the full request URL, a job-triggerable network failure (RST, DNS failure, non-2xx causing `retryOnServerError`, etc.) during the cache GET causes the complete presigned URL — including its signature query parameters — to be printed to the job's trace/stdout.

### Finding Description
The call path is: `CacheExtractorCommand.Execute` → `c.doRetry(c.download)` → `download` → `handlePresignedURL` → `downloadPresignedSequential` → `getCache(selectedURL)`.

In `getCache`:
```go
func (c *CacheExtractorCommand) getCache(rawURL string) (*http.Response, error) {
	resp, err := c.getClient().Get(rawURL)
	if err != nil {
		return nil, retryableErr{err: err}
	}
	...
``` [1](#0-0) 

The `err` returned by `http.Client.Get` is a `*url.Error`, whose `Error()` method formats as `Get "<rawURL>": <cause>`, embedding the **full, unmodified** `rawURL` (including query-string signature). This is wrapped in `retryableErr{err: err}` but never sanitized — contrast with every other call site in this file that calls `url_helpers.CleanURL(selectedURL)` before logging (e.g. `downloadPresignedSequential`, `tryPresignedParallelDownload`, `handleGoCloudURL`), all of which only clean the URL used for the "Downloading ... from" success-path log line, not the error path.

`retryableErr.Error()` just delegates to the wrapped error's `Error()`:
```go
func (e retryableErr) Error() string {
	return e.err.Error()
}
``` [2](#0-1) 

This propagates to two logging sites that never sanitize it:
1. On each retry iteration inside `doRetry`: `logrus.WithError(err).Warningln("Retrying...")`. [3](#0-2) 
2. On final failure in `Execute`: `warningln(err)` → `logrus.Warningln(args)`. [4](#0-3) [5](#0-4) 

Both use the default `logrus` formatter writing to the process's `stdout`, which for the `cache-extractor` helper command is captured directly into the job's trace output. No masking step exists for this because GitLab Runner's trace masking only strips values it is explicitly told to mask (CI/CD variables marked as masked, tokens the runner itself issued) — a dynamically generated presigned URL signature is not among those, so the only defense against leaking it is the `CleanURL` call, which is bypassed on this error path.

### Impact Explanation
Any network condition that makes `http.Client.Get` return an error (connection reset, DNS failure, TLS error, timeout) mid-download — occurring after the "Downloading ... from `<cleaned URL>`" success-log line was NOT yet reached, i.e., before headers are parsed — causes the full presigned URL, including its signature/expiry query parameters, to appear in the job trace. Anyone able to view that job's trace (which for GitLab CI is often visible to more people than the cache bucket credentials owner, e.g. other project members, or public projects) can extract the signature and, until it expires, fetch or overwrite the cache object directly against S3/GCS/Azure, bypassing GitLab Runner's project/job scoping.

### Likelihood Explanation
Preconditions are easily job-triggerable: an unprivileged pipeline author controls network conditions to the extent that they can, e.g., point the runner at a slow/unstable network path, or induce many cache-download retries where at some point the request fails transiently (real-world flaky networks, proxy resets, timeouts). No special runner privilege is required — only that a job normally downloading a cache via presigned URL experiences a `Get` failure. This is a realistic and repeatable condition (retries themselves, per `doRetry`, will surface this on every failed attempt, not just the final one), so the leak is not a rare corner case.

### Recommendation
Sanitize the URL before wrapping/logging any error derived from `http.Client.Get`/`Do` calls with a raw signed URL. Specifically:
- In `getCache`, catch the error, replace any embedded raw URL with `url_helpers.CleanURL(rawURL)` before wrapping in `retryableErr` (e.g. construct a new error using `fmt.Errorf("request failed: %w", ...)` with the raw `*url.Error`'s `Err` field only, or explicitly strip `err.(*url.Error).URL`).
- Do the same in `presignedRangeFetchChunk`'s `http.NewRequest`/`Do` error paths and any other call sites using presigned URLs (`fetchPresignedTimestamp`, `tryPresignedParallelDownload`).
- Ensure `doRetry`'s `logrus.WithError(err).Warningln("Retrying...")` and `Execute`'s `warningln(err)` never receive an error whose `Error()` string contains unsanitized query parameters.

### Proof of Concept
```go
func TestGetCacheDoesNotLeakSignedURLOnNetworkError(t *testing.T) {
    // presigned URL with a fake signature query param
    signedURL := "https://bucket.s3.amazonaws.com/cache.zip?X-Amz-Signature=SECRETSIG1234"

    c := &CacheExtractorCommand{
        URL:  signedURL,
        File: filepath.Join(t.TempDir(), "cache.zip"),
        retryHelper: retryHelper{Retry: 0},
    }

    // Force http.Client.Get to fail with a *url.Error embedding the full URL,
    // e.g. by pointing at an unroutable host/port that resets the connection.
    c.URL = strings.Replace(signedURL, "bucket.s3.amazonaws.com", "127.0.0.1:1", 1)

    r, w, _ := os.Pipe()
    origStdout := os.Stdout
    os.Stdout = w
    defer func() { os.Stdout = origStdout }()

    err := c.doRetry(c.download)
    _ = w.Close()
    out, _ := io.ReadAll(r)

    require.Error(t, err)
    // Assert the raw signature never appears in the error string used for logging.
    assert.NotContains(t, err.Error(), "X-Amz-Signature=SECRETSIG1234")
    assert.NotContains(t, string(out), "X-Amz-Signature=SECRETSIG1234")
}
```
Expected current behavior (bug): `err.Error()` contains the full `c.URL` including `X-Amz-Signature=SECRETSIG1234`, and once routed through `warningln`/`logrus`, the raw signature would be written to stdout/trace — the assertions fail, confirming the leak. After applying the recommended fix (cleaning the URL before wrapping the error), the assertions pass.

### Citations

**File:** commands/helpers/cache_extractor.go (L192-204)
```go
func (c *CacheExtractorCommand) getCache(rawURL string) (*http.Response, error) {
	resp, err := c.getClient().Get(rawURL)
	if err != nil {
		return nil, retryableErr{err: err}
	}

	if resp.StatusCode == http.StatusNotFound {
		_ = resp.Body.Close()
		return nil, os.ErrNotExist
	}

	return resp, retryOnServerError(resp)
}
```

**File:** commands/helpers/cache_extractor.go (L635-639)
```go
	if c.URL != "" || c.GoCloudURL != "" {
		err := c.doRetry(c.download)
		if err != nil {
			warningln(err)
		}
```

**File:** commands/helpers/cache_extractor.go (L666-669)
```go
func warningln(args interface{}) {
	logrus.Warningln(args)
	logrus.Exit(1)
}
```

**File:** commands/helpers/retry_helper.go (L64-66)
```go
func (e retryableErr) Error() string {
	return e.err.Error()
}
```

**File:** commands/helpers/retry_helper.go (L68-83)
```go
func (r *retryHelper) doRetry(handler func(int) error) error {
	err := handler(0)

	for retry := 1; retry <= r.Retry; retry++ {
		if _, ok := err.(retryableErr); !ok {
			return err
		}

		time.Sleep(r.RetryTime)
		logrus.WithError(err).Warningln("Retrying...")

		err = handler(retry)
	}

	return err
}
```
