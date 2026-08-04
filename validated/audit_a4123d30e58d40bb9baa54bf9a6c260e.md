### Title
Cross-tenant router discovery cache in `Client.getRouterDiscovery` leaks one RunnerConfig's router `ServerURL`/`TLSData` to another RunnerConfig sharing the same process - (File: router/client.go)

### Summary
`router.Client` is instantiated once per process (`newClient` in `main.go`) and shared as the single `common.Network` implementation for every `[[runners]]` entry in `config.toml`. Its `disco`/`discoExpiresAt` fields are cached on the `Client` struct itself, not keyed by `config.Token`/`config.URL`, so `getRouterDiscovery` can return one RunnerConfig's discovered router `ServerURL`/`TLSData` to a different RunnerConfig's `RequestJob` call for up to `discoveryTTL` (1 hour).

### Finding Description
`Client` is created once in `main.go`'s `newClient` and reused across the whole runner process: [1](#0-0) . This single `Client` is used by the multi-runner manager, which calls `RequestJob` for every `RunnerConfig` in `config.toml` against the same `common.Network` instance (`commands/multi.go` calls `RequestJob`).

Inside the client, `getRouterDiscovery` guards discovery with a mutex, but the cache fields `disco` and `discoExpiresAt` are plain fields on `Client`, not a map keyed by any per-config identity such as token or server URL: [2](#0-1) .

`getRouterDiscovery` checks only `c.discoExpiresAt.After(time.Now())` and, if the TTL hasn't expired, returns the cached `c.disco` regardless of which `RunnerConfig` is asking: [3](#0-2) 

`RequestJob` then dials the router using this (possibly wrong-tenant) discovery data — `disco.ServerURL` and `config.Token`/TLS files from the *current* config are combined with a `ServerURL`/`TLSData` that may have been discovered for a *different* config: [4](#0-3)  and the response's `TLSData` is unconditionally set from the (possibly stale/foreign) `disco`: [5](#0-4) .

Concretely: if RunnerConfig A polls first, `getRouterDiscovery` calls `c.delegate.GetRouterDiscovery(ctx, configA)` and caches the result. If RunnerConfig B (a different project/tenant, different token, potentially different GitLab instance URL) polls within the next hour, `c.discoExpiresAt.After(time.Now())` is true, so B's call short-circuits and reuses A's cached `ServerURL`/`TLSData` without ever calling `c.delegate.GetRouterDiscovery(ctx, configB)`. B's job request is then dialed against A's discovered router endpoint using B's token, and the resulting job's `TLSData` is populated from A's discovery.

No existing check mitigates this: the mutex only prevents concurrent corruption of the fields, not cross-config reuse; there is no per-token/per-URL key in the cache; `invalidateRouterDiscovery` only clears state on `Unimplemented` responses, not on a config switch.

### Impact Explanation
In a multi-runner `config.toml` (multiple `[[runners]]` sections with distinct tokens for different projects/tenants), one tenant's job-request traffic can be routed to and authenticated against a router endpoint/TLS identity discovered for a different tenant. This violates per-runner routing isolation: job routing decisions (which gRPC job-router endpoint and TLS material is trusted) leak across `RunnerConfig` boundaries, even though `RequestJob` still uses the correct `config.Token` for authentication to that (wrong) endpoint. The scoped impact is that RunnerConfig B's job request is dialed against RunnerConfig A's discovered router `ServerURL`/`TLSData`, i.e., cross-tenant reuse of routing/TLS material, contrary to the intended per-runner isolation of the job-router feature.

### Likelihood Explanation
This requires: (1) `UseJobRouter` feature flag enabled, (2) a `config.toml` with more than one `[[runners]]` entry served by the same process/`Client` instance (the common multi-runner deployment pattern), and (3) polling of at least two different RunnerConfigs occurring within the same `discoveryTTL` window (1 hour) — which is essentially guaranteed in any multi-runner deployment since runners poll continuously. No attacker action beyond normal job polling by GitLab (server-side discovery response) is needed; the bug is a config-scoping defect in the Runner itself, triggered purely by the existence of multiple runners in one process, not by malicious job input. This is a background operational condition rather than something an unprivileged CI job author can directly trigger or observe, but it is highly likely to occur automatically in standard multi-runner deployments and its effects (cross-tenant routing) are concrete and observable via server logs / dialed connections.

### Recommendation
Key the router discovery cache by a per-config identity (e.g., `config.Token`, `config.URL`, or a hash of both) using a `map[string]cachedDiscovery{disco, expiresAt}` protected by the existing mutex, instead of single `disco`/`discoExpiresAt` fields on `Client`. Ensure `invalidateRouterDiscovery` and TTL expiry are also scoped per key.

### Proof of Concept
```go
func TestGetRouterDiscovery_CrossConfigLeak(t *testing.T) {
    delegate := &mockDelegateGetRouterDiscovery{
        byToken: map[string]*common.RouterDiscovery{
            "token-A": {ServerURL: "grpc://router-A:1"},
            "token-B": {ServerURL: "grpc://router-B:1"},
        },
    }
    c := NewClient(delegate, t.TempDir(), "test")
    t.Cleanup(c.Shutdown)

    configA := common.RunnerConfig{RunnerCredentials: common.RunnerCredentials{Token: "token-A"}}
    configB := common.RunnerConfig{RunnerCredentials: common.RunnerCredentials{Token: "token-B"}}

    discoA := c.getRouterDiscovery(t.Context(), configA)
    require.Equal(t, "grpc://router-A:1", discoA.ServerURL)

    // Within discoveryTTL, config B's request should get its own router, not A's cached one.
    discoB := c.getRouterDiscovery(t.Context(), configB)
    assert.Equal(t, "grpc://router-B:1", discoB.ServerURL, "config B must not receive config A's cached router discovery")
}
```
This test fails against the current implementation because `getRouterDiscovery` returns the cached `c.disco` (router-A) for `configB` since `c.discoExpiresAt` has not elapsed, demonstrating the cross-config cache leak.

### Citations

**File:** main.go (L129-144)
```go
func newClient(executorProviders executors.Providers) (common.Network, func(), *network.APIRequestsCollector) {
	apiRequestsCollector := network.NewAPIRequestsCollector()
	certDir := commands.GetDefaultCertificateDirectory()

	mainClient := network.NewGitLabClient(
		network.WithAPIRequestsCollector(apiRequestsCollector),
		network.WithCertificateDirectory(certDir),
		network.WithExecutorProviderFunc(executorProviders.GetByName),
	)
	rc := router.NewClient(
		mainClient,
		certDir,
		common.AppVersion.UserAgent(),
	)
	return rc, rc.Shutdown, apiRequestsCollector
}
```

**File:** router/client.go (L66-75)
```go
type Client struct {
	common.Network // delegate all the methods except RequestJob()
	delegate       Delegate
	factory        *ClientConnFactory
	breaker        *circuitbreaker.Breaker
	metrics        *clientMetrics
	mu             sync.Mutex
	disco          *common.RouterDiscovery
	discoExpiresAt time.Time
}
```

**File:** router/client.go (L111-136)
```go
	disco := c.getRouterDiscovery(ctx, config)
	if disco == nil {
		return c.fallback(ctx, config, sessionInfo, fallbackNoDiscovery)
	}

	if !c.breaker.Allow() {
		return c.fallback(ctx, config, sessionInfo, fallbackBreakerOpen)
	}

	jobRequest := c.delegate.PrepareJobRequest(config, sessionInfo)
	jobRequestJSON, err := json.Marshal(jobRequest)
	if err != nil {
		// The router was never contacted, so there's no success/failure to record;
		// Abort releases the half-open trial Allow() may have granted.
		c.breaker.Abort()
		config.Log().WithError(err).Error("json.Marshal()")
		return nil, false
	}

	client, err := c.factory.Dial(DialTarget{
		URL:         disco.ServerURL,
		Token:       config.Token,
		TLSCAFile:   config.TLSCAFile, // use the same TLS bits as for the main GitLab URL
		TLSCertFile: config.TLSCertFile,
		TLSKeyFile:  config.TLSKeyFile,
	})
```

**File:** router/client.go (L230-231)
```go
	}
	response.TLSData = disco.TLSData
```

**File:** router/client.go (L239-253)
```go
func (c *Client) getRouterDiscovery(ctx context.Context, config common.RunnerConfig) *common.RouterDiscovery {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.discoExpiresAt.After(time.Now()) {
		c.metrics.recordCacheEvent(cacheHit)
		return c.disco
	}
	c.metrics.recordCacheEvent(cacheMiss)
	c.disco = c.delegate.GetRouterDiscovery(ctx, config)
	c.discoExpiresAt = time.Now().Add(discoveryTTL)
	if c.disco != nil {
		config.Log().Info("Using job router at " + c.disco.ServerURL)
	}
	return c.disco
}
```
