### Title
Unbounded per-cache-operation `GetBucketLocation` calls when `BucketLocation` is unset amplify into a shared-bucket AWS throttling DoS - (File: cache/s3v2/s3.go)

### Summary
When an operator's S3 cache config omits `BucketLocation` (a legitimate, common configuration), `newS3Client` deliberately bypasses the `s3ClientCache` and rebuilds a brand-new S3 client via `newRawS3Client` → `detectBucketLocation` on every single call, issuing a real AWS `GetBucketLocation` API request each time. Because each cache-resolution call in the job pipeline (`cache.GetAdapter` → `s3v2.New` → `newS3Client`) triggers this uncached path, a pipeline author can multiply the number of real AWS API calls per job by defining many `cache:` entries and/or using `parallel:`, producing a burst of `GetBucketLocation` requests against the shared bucket.

### Finding Description
`newS3Client` explicitly special-cases empty `BucketLocation`: [1](#0-0) 

This routes every call straight to `buildS3Client` → `newRawS3Client`, which calls `detectBucketLocation` whenever `s3Config.BucketLocation == ""`: [2](#0-1) 

`detectBucketLocation` performs a real, network-bound `client.GetBucketLocation` AWS API call with no caching, rate limiting, or backoff: [3](#0-2) 

`s3v2.New` (the cache adapter constructor registered for the `s3v2` cache type) calls `newS3Client(s3Config)` on every invocation: [4](#0-3) 

`New`/`newS3Client` is invoked once per cache-key resolution call from the shell/build pipeline: `cache.GetAdapter` is called separately for primary download, alternate download (`FF_HASH_CACHE_KEYS` toggling), primary upload, and alternate upload: [5](#0-4) [6](#0-5) [7](#0-6) 

A pipeline author (unprivileged job/pipeline controller) fully controls the number of `cache:` stanzas in `.gitlab-ci.yml` and can use `parallel:` to fan out many concurrent job instances. Each additional cache key/entry and each parallel job instance produces its own independent `New()`/`newS3Client()`/`detectBucketLocation()` call, so the number of real `GetBucketLocation` AWS requests scales directly with attacker-controlled YAML structure (number of cache stanzas × parallel job count × pipeline trigger rate), not with actual data transferred.

The code comments confirm this is intentional design (self-healing on transient errors, avoiding pinning a stale fallback), but it lacks any per-runner/per-process throttling, debouncing, or minimum-interval cap on `detectBucketLocation` calls, so nothing prevents a job author from generating an unbounded burst of AWS API calls in a short window.

### Impact Explanation
If the burst is large enough to hit AWS-side throttling on `GetBucketLocation` for the shared bucket, `detectBucketLocation` falls back to `fallbackBucketLocation` ("us-east-1") on error: [8](#0-7) 

For any other project/job on the same runner or GitLab instance that also has `BucketLocation` unset and shares the same bucket, this fallback can produce a region mismatch versus the bucket's actual region, causing subsequent presigned URL requests (SigV4-signed against the wrong region) to fail with signature/region errors on the real S3 data plane — degrading cache availability for jobs unrelated to the attacker's own job, and persisting until the AWS-side throttle window clears (i.e., outliving the attacker's own job/cancellation). This matches the scoped impact: a single job's cache-configuration/traffic pattern degrading shared cache availability for other projects.

### Likelihood Explanation
Preconditions: operator has an S3 cache config without an explicit `BucketLocation` (explicitly called out in the question as common/valid, and the code itself documents this as a normal, non-admin-error configuration) and multiple projects/jobs share the same bucket/runner. Given that, the attacker only needs ordinary pipeline authoring capability (`.gitlab-ci.yml` control, `parallel:` keyword) — no privileged access. This is fully repeatable: each pipeline run/job with N cache stanzas produces N (or up to 4N counting alternate/download/upload combinations) `GetBucketLocation` calls, and pipeline triggers or `parallel:` fan-out can be repeated indefinitely.

### Recommendation
Cache the detected bucket location (with a short TTL and per-config key, similar to the AssumeRole credential cache) even when `BucketLocation` is unset, instead of bypassing `s3ClientCache` entirely; add a per-runner rate limiter/backoff around `detectBucketLocation` so a burst of concurrent cache operations collapses into a single in-flight AWS call (similar to the existing `sync.Once`-based `clientInit`/semaphore pattern used for AssumeRole), and add a minimum re-detection interval instead of doing a full detection on every uncached call.

### Proof of Concept
Go test in `cache/s3v2/s3_test.go`:
1. Create a `CacheS3Config` with `BucketLocation: ""` and a mock/fake S3 endpoint (as already done via `gofakes3` in existing tests) instrumented to count `GetBucketLocation` invocations.
2. Simulate a job with N cache stanzas by calling `newS3Client(s3Config)` N times in a tight loop (mirroring N calls to `cache.GetAdapter`/`s3v2.New` for N `cache:` entries), plus doubling for alternate keys.
3. Assert that the number of `GetBucketLocation` calls to the mock backend equals N (or 2N/4N), demonstrating unbounded linear scaling with attacker-controlled cache-stanza count rather than a fixed per-runner cap.
4. Repeat with concurrent goroutines simulating `parallel:` jobs to show the burst multiplies further, and compare against a proposed fixed cap (e.g., assert failure once a rate limiter/cache is introduced and the count stays ≤ cap regardless of N).

### Citations

**File:** cache/s3v2/s3.go (L450-453)
```go
	bucketLocation := s3Config.BucketLocation
	if bucketLocation == "" {
		bucketLocation = detectBucketLocation(s3Config, options...)
	}
```

**File:** cache/s3v2/s3.go (L501-531)
```go
func detectBucketLocation(s3Config *cacheconfig.CacheS3Config, optFuncs ...func(*config.LoadOptions) error) string {
	// The 30 seconds timeout here is arbritrary
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	// When s3 is configured with an IAM profile, a default region must be set
	// We therefore set the default region to us-east-1
	configOpts := append(
		[]func(*config.LoadOptions) error{
			config.WithRegion(fallbackBucketLocation),
		},
		optFuncs...,
	)

	cfg, err := config.LoadDefaultConfig(ctx, configOpts...)
	if err != nil {
		return fallbackBucketLocation
	}

	endpoint := s3Config.GetEndpoint()
	effectiveEndpoint := DEFAULT_AWS_S3_ENDPOINT
	client := s3.NewFromConfig(cfg, func(o *s3.Options) {
		if endpoint != "" && endpoint != DEFAULT_AWS_S3_ENDPOINT {
			o.BaseEndpoint = aws.String(endpoint)
			effectiveEndpoint = endpoint
		}
		o.UsePathStyle = s3Config.PathStyleEnabled()
	})
	output, err := client.GetBucketLocation(ctx, &s3.GetBucketLocationInput{
		Bucket: aws.String(s3Config.BucketName),
	})
```

**File:** cache/s3v2/s3.go (L538-541)
```go
	if err != nil {
		logEntry.WithError(err).Warning("Failed to detect S3 bucket location, falling back to default region")
		return fallbackBucketLocation
	}
```

**File:** cache/s3v2/s3.go (L691-700)
```go
	// Configs without an explicit BucketLocation trigger bucket-region
	// auto-detection inside newRawS3Client, which silently falls back to
	// us-east-1 on transient errors. Caching would pin that fallback client
	// for the process lifetime, so such configs must never touch
	// s3ClientCache, regardless of options. This matches the effective
	// pre-fix behavior: pointer keys never produced cross-job reuse, and
	// per-call detection lets a transient failure self-heal.
	if s3Config.BucketLocation == "" {
		return buildS3Client(s3Config, options...)
	}
```

**File:** cache/s3v2/adapter.go (L163-182)
```go
func New(config *cacheconfig.Config, timeout time.Duration, objectName string) (cache.Adapter, error) {
	s3Config := config.S3
	if s3Config == nil {
		return nil, fmt.Errorf("missing S3 configuration")
	}

	client, err := newS3Client(s3Config)
	if err != nil {
		return nil, fmt.Errorf("error while creating S3 cache storage client: %w", err)
	}

	a := &s3Adapter{
		config:     s3Config,
		timeout:    timeout,
		objectName: strings.TrimLeft(objectName, "/"),
		client:     client,
	}

	return a, nil
}
```

**File:** common/build_step_dispatch.go (L195-224)
```go
func cacheDownloadDescriptor(ctx context.Context, build *Build, sharded bool) func(string) (cacheprovider.Descriptor, error) {
	return func(cacheKey string) (cacheprovider.Descriptor, error) {
		adapter := cache.GetAdapter(build.Runner.Cache, build.GetBuildTimeout(), build.Runner.ShortDescription(), fmt.Sprintf("%d", build.JobInfo.ProjectID), cacheKey, sharded)

		goCloudURL, err := adapter.GetGoCloudURL(ctx, false)
		if goCloudURL.URL != nil {
			return cacheprovider.Descriptor{
				GoCloudURL: true,
				URL:        goCloudURL.URL.String(),
				Env:        goCloudURL.Environment,
			}, err
		}

		url := adapter.GetDownloadURL(ctx)
		if url.URL == nil {
			return cacheprovider.Descriptor{}, nil
		}

		desc := cacheprovider.Descriptor{
			URL:     url.URL.String(),
			Headers: url.Headers,
		}

		if headURL := adapter.GetHeadURL(ctx); headURL.URL != nil {
			desc.HeadURL = headURL.URL.String()
		}

		return desc, nil
	}
}
```

**File:** shells/abstract.go (L424-443)
```go
func getCacheDownloadURLAndEnv(ctx context.Context, build *common.Build, cacheKey string) ([]string, map[string]string, error) {
	adapter := cache.GetAdapter(build.Runner.Cache, build.GetBuildTimeout(), build.Runner.ShortDescription(), fmt.Sprintf("%d", build.JobInfo.ProjectID), cacheKey, build.IsFeatureFlagOn(featureflags.HashCacheKeys))

	// Prefer Go Cloud URL if supported
	goCloudURL, err := adapter.GetGoCloudURL(ctx, false)

	if goCloudURL.URL != nil {
		return []string{"--gocloud-url", goCloudURL.URL.String()}, goCloudURL.Environment, err
	}

	if url := adapter.GetDownloadURL(ctx); url.URL != nil {
		args := []string{"--url", url.URL.String()}
		if headURL := adapter.GetHeadURL(ctx); headURL.URL != nil {
			args = append(args, "--head-url", headURL.URL.String())
		}
		return args, nil, nil
	}

	return []string{}, nil, nil
}
```

**File:** shells/abstract.go (L1605-1620)
```go
func getCacheUploadURLAndEnv(ctx context.Context, build *common.Build, cacheKey string, metadata map[string]string) ([]string, map[string]string, error) {
	adapter := cache.GetAdapter(build.Runner.Cache, build.GetBuildTimeout(), build.Runner.ShortDescription(), fmt.Sprintf("%d", build.JobInfo.ProjectID), cacheKey, build.IsFeatureFlagOn(featureflags.HashCacheKeys))

	// Prefer Go Cloud URL if supported
	goCloudURL, err := adapter.GetGoCloudURL(ctx, true)
	if goCloudURL.URL != nil {
		uploadArgs := []string{"--gocloud-url", goCloudURL.URL.String()}
		return uploadArgs, goCloudURL.Environment, err
	}

	adapter.WithMetadata(metadata)
	uploadURL := adapter.GetUploadURL(ctx)
	if uploadURL.URL == nil {
		return []string{}, nil, nil
	}

```
