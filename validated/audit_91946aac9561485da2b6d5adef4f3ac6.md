### Title
Unsanitized pre-signed cache URLs leak via error-path logging (`logrus.Warningln`/`logrus.Fatalln`/`WithError`) - ([File: commands/helpers/cache_extractor.go], [File: commands/helpers/cache_archiver.go])

### Summary
`url_helpers.CleanURL` is only applied to URLs used in *success-path* log messages (e.g. "Downloading ... from `cleanedURL`"), but any error returned by `net/http` (`Get`/`Head`/`Do`) is a `*url.Error` whose `Error()` string embeds the original, unsanitized request URL — including pre-signed query-string credentials (e.g. `X-Amz-Signature`, `X-Amz-Credential`, SAS tokens). These raw errors are logged directly via `logrus.Warningln`, `logrus.Fatalln`, and `logrus.WithError(err)` without ever being passed through `CleanURL`.

### Finding Description
In `commands/helpers/cache_extractor.go`, `getCache` calls `c.getClient().Get(rawURL)` and wraps the raw client error in `retryableErr{err: err}` without sanitizing it: [1](#0-0) 
That error propagates unchanged through `downloadPresignedSequential` → `handlePresignedURL` → `download` → `doRetry`, and is finally printed via `warningln(err)` in `Execute`: [2](#0-1) [3](#0-2) 

`warningln` simply calls `logrus.Warningln(args)`, which for a `*url.Error`-wrapping value formats it via `Error()`, and Go's standard library `url.Error.Error()` implementation embeds the exact URL string passed to `http.Client.Get/Head/Do` (query string included), because it is constructed as `fmt.Sprintf("%s %q: %s", op, url, err)`.

Similarly, `primaryPresignedExists` in `commands/helpers/cache_archiver.go` performs `c.getClient().Head(c.CheckURL)` and, on failure, logs via `logrus.WithError(err).Warningln(...)` — again the raw, credential-bearing `CheckURL` is embedded in the error and reaches the trace: [4](#0-3) 

By contrast, `CleanURL` (which strips `User`, `RawQuery`, and `Fragment`) is only invoked on the success path, e.g. before constructing "Downloading ... from" messages: [5](#0-4) [6](#0-5) 

There is no sanitization step applied to any error object before it reaches `logrus`. Any network-level failure that causes Go's `net/http`/`net/url` machinery to construct a `*url.Error` (DNS failure, TLS handshake failure, connection refused, a redirect through an attacker-influenced proxy, context deadline exceeded, etc.) will carry the full request URL — including query-string credentials — into that error, and the code has no path that strips it before logging.

### Impact Explanation
An unprivileged pipeline author can craft a job/cache configuration (e.g., pointing the cache download to a host that will reliably fail — non-routable IP, TLS-broken endpoint, or a proxy set via `HTTP_PROXY`/`HTTPS_PROXY` env vars honored by `http.ProxyFromEnvironment` in `prepareTransport`) so that the presigned GET/HEAD/PUT request errors out. The resulting `logrus.Warningln`/`Fatalln`/`WithError` call prints the live pre-signed cache URL — a bearer-token-equivalent credential — into the job trace/log, which is visible to the job author (and to anyone with pipeline log access). That credential can then be used out-of-band to read or write the associated cache object until the pre-signed URL expires — matching the scoped impact of a token/session hijack for that cache resource. [7](#0-6) 

### Likelihood Explanation
This is easily reproducible by an unprivileged job author: no special runner configuration is required beyond a normal cache configuration and control of network conditions reachable from the job container (e.g., an unreachable/failing cache URL substitute, TLS certificate mismatch, or proxy interception). GitLab CI users cannot normally control the actual pre-signed cache URL value sent by GitLab, but they can trivially force request failures (e.g., via `HTTP_PROXY`/`NO_PROXY` manipulation or job-controlled DNS/network conditions) that trigger this logging path on every retry attempt, making it a reliable, low-effort exploit.

### Recommendation
Sanitize error messages before logging anywhere a pre-signed/credentialed URL could be embedded:
- Wrap/replace errors from `c.getClient().Get/Head/Do` so any `*url.Error` has its `URL` field cleaned via `url_helpers.CleanURL` before formatting, or catch `*url.Error` specifically and reconstruct the error string using the cleaned URL.
- Update `retryableErr` (and any error type wrapping HTTP errors in `cache_extractor.go`/`cache_archiver.go`) to sanitize the underlying error text at construction time.
- Apply the same sanitization to `logrus.WithError(err)` call sites in `primaryPresignedExists`, `openAlternateGoCloudBucket` failures, and any other error-path logging that could carry a request URL.

### Proof of Concept
Go unit test sketch:
```go
func TestGetCacheErrorDoesNotLeakCredentials(t *testing.T) {
    presignedURL := "https://example-bucket.s3.amazonaws.com/obj?X-Amz-Signature=SECRETVALUE&X-Amz-Credential=AKIAFAKE"
    c := &CacheExtractorCommand{URL: presignedURL}
    // Force a client-level failure: use an unroutable host or TLS mismatch
    _, err := c.getCache(presignedURL)
    require.Error(t, err)
    assert.NotContains(t, err.Error(), "X-Amz-Signature=SECRETVALUE",
        "raw pre-signed credential must never appear in a propagated error")
}
```
Integration/log-capture PoC: run `cache-extractor` (or `cache-archiver`) pointed at a pre-signed URL with an unreachable host, capture `logrus` output (redirect `logrus.SetOutput`), and assert the captured trace does not contain the query string component of the configured URL. Expected current (buggy) behavior: the raw query string appears in the `Warningln`/`Fatalln` output; expected fixed behavior: only the `CleanURL`-sanitized form (no query string) appears.

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

**File:** commands/helpers/cache_extractor.go (L308-327)
```go
func (c *CacheExtractorCommand) downloadPresignedSequential() error {
	selectedURL := c.selectPresignedURL()

	resp, err := c.getCache(selectedURL)
	if err != nil {
		return err
	}
	defer func() { _ = resp.Body.Close() }()

	upToDate, date := checkIfUpToDate(c.File, resp)
	if upToDate {
		logrus.Infoln(filepath.Base(c.File), "is up to date")
		return nil
	}

	etag := resp.Header.Get("ETag")
	cleanedURL := url_helpers.CleanURL(selectedURL)
	contentLength := getRemoteCacheSize(resp)

	return c.downloadAndSaveCache(resp.Body, date, etag, cleanedURL, contentLength, headersToCacheMetadata(resp.Header))
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

**File:** commands/helpers/cache_archiver.go (L397-407)
```go
func (c *CacheArchiverCommand) primaryPresignedExists() bool {
	resp, err := c.getClient().Head(c.CheckURL)
	if err != nil {
		logrus.WithError(err).Warningln("Failed to check primary cache existence via HEAD request, assuming absent")
		return false
	}
	defer func() { _ = resp.Body.Close() }()
	exists := resp.StatusCode == http.StatusOK
	logrus.WithField("status", resp.StatusCode).Debugln("Primary cache HEAD request completed")
	return exists
}
```

**File:** helpers/url/clean_url.go (L5-14)
```go
func CleanURL(value string) (ret string) {
	u, err := url.Parse(value)
	if err != nil {
		return
	}
	u.User = nil
	u.RawQuery = ""
	u.Fragment = ""
	return u.String()
}
```

**File:** commands/helpers/cache_client.go (L23-36)
```go
func (c *CacheClient) prepareTransport() {
	c.Transport = &http.Transport{
		Proxy: http.ProxyFromEnvironment,
		DialContext: (&net.Dialer{
			Timeout:   30 * time.Second,
			KeepAlive: 30 * time.Second,
		}).DialContext,
		IdleConnTimeout:       90 * time.Second,
		TLSHandshakeTimeout:   10 * time.Second,
		ExpectContinueTimeout: 10 * time.Second,
		ResponseHeaderTimeout: 30 * time.Second,
		DisableCompression:    true,
	}
}
```
