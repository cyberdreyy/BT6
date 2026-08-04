### Title
Job-router discovery cache is keyed globally on `Client`, not per `RunnerConfig`, so a stale/attacker-influenced `ServerURL` from one tenant can be served to another tenant's jobs for up to one hour - (File: `router/client.go`)

### Summary
`gitlab-runner` runs a single `router.Client` instance for the entire process (constructed once in `newClient` in `main.go` and used for all configured runners), but `Client.disco`/`Client.discoExpiresAt` is a single unkeyed pair shared across every call to `getRouterDiscovery`, regardless of which `RunnerConfig` (i.e., which registered runner/project/token) is asking. Within the one-hour `discoveryTTL`, any `RunnerConfig` whose `RequestJob` happens to run after another tenant's `RunnerConfig` populated the cache will reuse that tenant's discovery result instead of re-querying `delegate.GetRouterDiscovery` for its own token/URL.

### Finding Description
`Client` is instantiated once per `gitlab-runner` process: [1](#0-0) 

That single `rc` (`*router.Client`) is used as the `common.Network` implementation for every `RunnerConfig` the multi-runner manager polls (`commands/multi.go` iterates over all configured runners calling `RequestJob` against the same network client). Inside `Client`, discovery caching state is not namespaced by tenant identity at all: [2](#0-1) 

The cache-check/populate logic only looks at `discoExpiresAt` — it never incorporates `config.Token`, `config.URL`, or any other tenant-identifying field from the `RunnerConfig` passed in: [3](#0-2) 

`RequestJob` calls `c.getRouterDiscovery(ctx, config)` for whichever `RunnerConfig` is currently being polled, then dials `disco.ServerURL` using that same cached value: [4](#0-3) [5](#0-4) 

Exploit flow:
1. Runner process has ≥2 `RunnerConfig`s registered (e.g., Project A and Project B, distinct tokens), sharing the single process-wide `router.Client`.
2. Project A polls first; `getRouterDiscovery` misses cache, calls `delegate.GetRouterDiscovery(ctx, configA)`, gets `ServerURL = X`, and caches it in `c.disco`/`c.discoExpiresAt` for up to one hour — with no association to Project A.
3. Attacker (an unprivileged Project A user/pipeline author) triggers or benefits from Project A's discovery endpoint changing shortly after (e.g., project moved off shared router, endpoint rotated, or the attacker otherwise causes a legitimate change to A's routing target such that `X` becomes stale/re-registrable by a third party).
4. Because the cache is keyed on the `Client` instance and not per-`RunnerConfig`, when Project B's `RunnerConfig` polls next (same process), `c.discoExpiresAt.After(time.Now())` is still true, so `getRouterDiscovery` returns the stale `disco` cached from Project A — never calling `delegate.GetRouterDiscovery(ctx, configB)` to fetch B's own, correct/current endpoint.
5. Project B's job request (and its `jobRequest` payload, which can contain job variables/tokens/artifacts metadata) is dialed to `X`, the endpoint originally scoped to Project A, which may since have been re-registered/hijacked.

No existing check mitigates this: there is no per-token/per-URL cache partitioning, and `invalidateRouterDiscovery` is only invoked on explicit `Unimplemented` router responses — not on a `RunnerConfig` switch.

### Impact Explanation
Job payloads (which can include CI variables and other job-scoped data marshaled into `jobRequestJSON`) for one project can be dialed to a `ServerURL` that was discovered for a *different* project, for up to `discoveryTTL` (1 hour) after the first discovery. If that endpoint is later reassigned/rotated (an event outside the runner's control but plausible in dynamic router deployments), a subsequent tenant's job traffic is misrouted to it, potentially exposing that job's data to whatever now answers at that endpoint. This is a cross-tenant confidentiality violation within a single shared `gitlab-runner` process serving multiple registered runners/projects.

### Likelihood Explanation
Preconditions: a single `gitlab-runner` process configured with multiple `RunnerConfig`s (a common deployment pattern — one runner manager, multiple registered runners/projects) with `UseJobRouter` feature flag enabled, and a discovery endpoint that can legitimately change within the TTL window (router rollout/migration, project re-homed to a different router shard). This is fully within normal multi-tenant runner operation — no privileged access needed — the only "attacker action" is being the first tenant to poll and something changing about routing thereafter, which need not even be attacker-triggered; an attacker only needs to observe/benefit from it. Repeatable in a deterministic unit test.

### Recommendation
Key the discovery cache per tenant identity (e.g., a map from `config.Token` or `config.GetUniqueID()`/`config.URL` to its own `disco`/`expiresAt` entry) instead of a single shared pair on `Client`. Alternatively, if a per-`Client` `router.Client` should genuinely be per-`RunnerConfig`, restructure `main.go`/`commands/multi.go` so each `RunnerConfig` gets (or the router client is invoked with) a scope that cannot leak across tenants, and invalidate/bypass the cache whenever the calling `RunnerConfig`'s token differs from the one that populated the cache.

### Proof of Concept
Go unit test in `router/client_test.go`:
```go
func TestGetRouterDiscovery_CrossTenantCacheLeak(t *testing.T) {
    // fakeDelegate.GetRouterDiscovery returns a ServerURL derived from config.Token,
    // e.g. "grpc://router-for-" + config.Token
    delegate := &fakeDelegate{}
    rc := NewClient(delegate, t.TempDir(), "runner-test")

    configA := newConfig("https://gitlab.example.com") // Token: "tokenA"
    configA.Token = "tokenA"
    configB := configA
    configB.Token = "tokenB"

    discoA := rc.getRouterDiscovery(t.Context(), configA)
    require.Equal(t, "grpc://router-for-tokenA", discoA.ServerURL)
    require.Equal(t, 1, delegate.callCount) // first call, cache miss

    // Within TTL, a *different* RunnerConfig/tenant polls.
    discoB := rc.getRouterDiscovery(t.Context(), configB)

    // BUG: cache is shared, so discoB incorrectly equals discoA's stale/wrong-tenant URL,
    // and the delegate was never queried for tokenB.
    assert.Equal(t, 2, delegate.callCount,
        "getRouterDiscovery must re-query the delegate per distinct RunnerConfig.Token, not reuse another tenant's cached discovery")
    assert.Equal(t, "grpc://router-for-tokenB", discoB.ServerURL)
}
```
Expected result on the current code: the assertion `delegate.callCount == 2` fails (it stays `1`), and `discoB.ServerURL` incorrectly equals `"grpc://router-for-tokenA"`, demonstrating the cross-tenant cache leak.

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

**File:** router/client.go (L103-114)
```go
func (c *Client) RequestJob(ctx context.Context, config common.RunnerConfig, sessionInfo *common.SessionInfo) (*spec.Job, bool) {
	if !config.IsFeatureFlagOn(featureflags.UseJobRouter) {
		return c.delegate.RequestJob(ctx, config, sessionInfo)
	}

	// Resolve discovery before the breaker gate so a "no router" result can't
	// strand a half-open trial. Every path past Allow() must then resolve the
	// trial: record an outcome once the router is reached, or Abort if we bail.
	disco := c.getRouterDiscovery(ctx, config)
	if disco == nil {
		return c.fallback(ctx, config, sessionInfo, fallbackNoDiscovery)
	}
```

**File:** router/client.go (L130-136)
```go
	client, err := c.factory.Dial(DialTarget{
		URL:         disco.ServerURL,
		Token:       config.Token,
		TLSCAFile:   config.TLSCAFile, // use the same TLS bits as for the main GitLab URL
		TLSCertFile: config.TLSCertFile,
		TLSKeyFile:  config.TLSKeyFile,
	})
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
