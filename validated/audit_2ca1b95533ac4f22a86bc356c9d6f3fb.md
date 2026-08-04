### Title
Unbounded `CACHE_REQUEST_TIMEOUT` job variable lets a job mint long-lived Azure cache SAS tokens - ([File: cache/azure/azure.go])

### Summary
`CACHE_REQUEST_TIMEOUT` is a job-controlled CI/CD variable that is parsed into `BuildSettings.CacheRequestTimeout` with no upper-bound validation, unlike other tunables (`GET_SOURCES_ATTEMPTS`, `ARTIFACT_DOWNLOAD_ATTEMPTS`, etc.) which are explicitly clamped to `[1, 10]`. This unbounded value flows through `cache.GetAdapter`/the Azure `Factory` into `signedURLOptions.Timeout`, which is used directly as `ExpiryTime` for the SAS token in `generateBlobSignatureValues`, allowing a job to mint a cache-access token valid far beyond the job's lifetime.

### Finding Description
The `CACHE_REQUEST_TIMEOUT` CI/CD variable is read and validated in `validateVariables` at [1](#0-0)  with `DefaultCacheRequestTimeout` (10 minutes) as fallback, but no range/maximum check exists for it. Compare this with `validateAttemptSettings`, which explicitly clamps other job-settable attempt counters to `[1, 10]` via the `clamp` helper: [2](#0-1)  — `CacheRequestTimeout` is conspicuously absent from any such clamping logic.

The unclamped value is exposed via `Build.GetCacheRequestTimeout()` [3](#0-2)  and is used (via shells/abstract.go, not fully traced here) as the `timeout` argument passed into `cache.GetAdapter`, which forwards it unchanged to the adapter factory: [4](#0-3) . For the Azure adapter, this becomes `azureAdapter.timeout`, which is placed straight into `signedURLOptions.Timeout` when generating a SAS token: [5](#0-4) .

Finally, `generateBlobSignatureValues` uses this caller-supplied timeout, with no cap, to compute the token's `ExpiryTime`: [6](#0-5) . There is no independent maximum enforced on `ExpiryTime` anywhere in this call chain — it is purely `time.Now().Add(o.Timeout)`.

Since `CacheRequestTimeout` is an `int` interpreted as minutes (see `CacheClient.prepareClient`, which does `time.Duration(timeout) * time.Minute`: [7](#0-6) ), a job author can set `CACHE_REQUEST_TIMEOUT` to an arbitrarily large integer (e.g., years' worth of minutes) in `.gitlab-ci.yml` `variables:` and have the runner mint a SAS token with an `ExpiryTime` far in the future — for either GET (download) or PUT (upload) permissions depending on the `Method` used when this code runs.

### Impact Explanation
For Azure cache backends using the `userDelegationKeySigner`/`accountKeySigner` path, this lets a job (or anything the job leaks the token to, e.g. via job logs, artifacts, or a malicious build script) obtain a cache-object SAS token that remains valid long after the job and pipeline finish — potentially indefinitely. Because the token grants `Write` (on PUT/upload) or `Read` (on GET/download) permissions scoped to that cache blob path, an attacker holding the leaked token could overwrite or read the project's cache object at any point in the future, independent of pipeline retention or job lifetime, causing persistent cross-job cache poisoning/disruption for that project's cache namespace.

### Likelihood Explanation
This requires only that: (1) the runner is configured to use the Azure cache backend, and (2) the job can set CI/CD variables (a job author normally can set `variables:` in `.gitlab-ci.yml`, subject to runner/project policy). No privilege escalation or admin action is needed — this is squarely within the "unprivileged pipeline author" threat model. The bug is fully deterministic and repeatable: setting `CACHE_REQUEST_TIMEOUT` to a large value on every run produces a correspondingly long-lived `ExpiryTime`.

### Recommendation
Enforce an upper bound on `CacheRequestTimeout` in `validateAttemptSettings` (or a similar validation step) analogous to the existing `clamp` used for attempt counters, and/or independently cap `ExpiryTime` in `generateBlobSignatureValues` (e.g., `min(time.Now().Add(o.Timeout), time.Now().Add(maxSASLifetime))`) so that caller-supplied timeout values cannot push the SAS token expiry beyond a runner-enforced maximum (e.g., a small multiple of the default 10-minute cache timeout).

### Proof of Concept
Go unit test in `cache/azure/azure_test.go`:
```go
func TestGenerateBlobSignatureValues_ExpiryUpperBound(t *testing.T) {
    hugeTimeout := 100000 * time.Hour // simulate CACHE_REQUEST_TIMEOUT set to a huge value
    o := &signedURLOptions{
        ContainerName: "container",
        Method:        http.MethodPut,
        Timeout:       hugeTimeout,
    }

    values := generateBlobSignatureValues("object", o)

    maxAllowedExpiry := time.Now().Add(1 * time.Hour) // expected sane runner-enforced cap
    assert.True(t, values.ExpiryTime.Before(maxAllowedExpiry),
        "ExpiryTime should be capped independent of caller-supplied Timeout, got %v", values.ExpiryTime)
}
```
Expected current behavior: the assertion fails, because `ExpiryTime` is `time.Now().Add(hugeTimeout)` with no cap — demonstrating the missing upper-bound enforcement.

### Citations

**File:** common/build_settings.go (L134-153)
```go
func (b *Build) validateAttemptSettings() []error {
	var errs []error

	clamp := func(variable *int, varName string) {
		const minAttempts, maxAttempts = 1, 10
		val := max(minAttempts, min(maxAttempts, *variable))
		if val != *variable {
			*variable = val
			errs = append(errs, fmt.Errorf("%s: number of attempts out of the range [%d, %d], clamping to: %d", varName, minAttempts, maxAttempts, *variable))
		}
	}

	clamp(&b.buildSettings.ExecutorJobSectionAttempts, "EXECUTOR_JOB_SECTION_ATTEMPTS")
	clamp(&b.buildSettings.GetSourcesAttempts, "GET_SOURCES_ATTEMPTS")
	clamp(&b.buildSettings.ArtifactDownloadAttempts, "ARTIFACT_DOWNLOAD_ATTEMPTS")
	clamp(&b.buildSettings.RestoreCacheAttempts, "RESTORE_CACHE_ATTEMPTS")
	clamp(&b.buildSettings.SecretsRetrievalAttempts, "SECRETS_RETRIEVAL_ATTEMPTS")

	return errs
}
```

**File:** common/build_settings.go (L181-181)
```go
		validate(variables, "CACHE_REQUEST_TIMEOUT", &b.buildSettings.CacheRequestTimeout, DefaultCacheRequestTimeout),
```

**File:** common/build.go (L2274-2276)
```go
func (b *Build) GetCacheRequestTimeout() int {
	return b.Settings().CacheRequestTimeout
}
```

**File:** cache/cache.go (L27-72)
```go
func GetAdapter(config *cacheconfig.Config, timeout time.Duration, shortToken, projectId, key string, sharded bool) Adapter {
	if config == nil {
		return nopAdapter{}
	}

	if key == "" {
		logrus.Warning("Empty cache key. Skipping adapter selection.")
		return nopAdapter{}
	}

	// generate object path
	// runners get their own namespace, unless they're shared, in which case the
	// namespace is empty.
	namespace := ""
	if !config.GetShared() {
		namespace = path.Join("runner", shortToken)
	}
	basePath := path.Join(config.GetPath(), namespace, "project", projectId)

	// When sharded (i.e. FF_HASH_CACHE_KEYS is enabled), insert the first two
	// hex characters of the key as an intermediate path component. This
	// distributes objects across 256 distinct S3 prefixes per project, avoiding
	// 503 Slow Down responses caused by all cache objects sharing the same
	// prefix and landing on the same partition.
	var fullPath string
	if sharded {
		if len(key) < 2 {
			logrus.WithError(fmt.Errorf("cache key too short to shard (length %d)", len(key))).Error("Error while generating cache bucket.")
			return nopAdapter{}
		}
		fullPath = path.Join(basePath, key[:2], key)
	} else {
		fullPath = path.Join(basePath, key)
	}

	// The typical concerns regarding the use of strings.HasPrefix to detect
	// path traversal do not apply here. The detection here is made easier
	// as we're dealing with URL paths, not filepaths and we're ensuring that
	// the basepath has a final separator (the key can not be empty).
	// TestGenerateObjectName contains path traversal tests.
	if !strings.HasPrefix(fullPath, basePath+"/") {
		logrus.WithError(fmt.Errorf("computed cache path outside of project bucket. Please remove `../` from cache key")).Error("Error while generating cache bucket.")
		return nopAdapter{}
	}

	adapter, err := createAdapter(config, timeout, fullPath)
```

**File:** cache/azure/adapter.go (L108-113)
```go
	t, err := a.blobTokenGenerator(ctx, a.objectName, &signedURLOptions{
		ContainerName: a.config.ContainerName,
		Signer:        signer,
		Method:        method,
		Timeout:       a.timeout,
	})
```

**File:** cache/azure/azure.go (L179-195)
```go
func generateBlobSignatureValues(name string, o *signedURLOptions) sas.BlobSignatureValues {
	permissions := sas.BlobPermissions{Read: true}
	if o.Method == http.MethodPut {
		permissions = sas.BlobPermissions{Write: true}
	}

	// Set the desired SAS signature values.
	// See https://docs.microsoft.com/en-us/rest/api/storageservices/create-service-sas
	return sas.BlobSignatureValues{
		Protocol:      sas.ProtocolHTTPS, // Users MUST use HTTPS (not HTTP)
		StartTime:     time.Now().Add(-1 * time.Hour).UTC(),
		ExpiryTime:    time.Now().Add(o.Timeout).UTC(),
		Permissions:   permissions.String(),
		ContainerName: o.ContainerName,
		BlobName:      name,
	}
}
```

**File:** commands/helpers/cache_client.go (L15-21)
```go
func (c *CacheClient) prepareClient(timeout int) {
	if timeout > 0 {
		c.Timeout = time.Duration(timeout) * time.Minute
	} else {
		c.Timeout = time.Duration(common.DefaultCacheRequestTimeout) * time.Minute
	}
}
```
