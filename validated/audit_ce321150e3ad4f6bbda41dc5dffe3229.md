### Title
Global, un-keyed router discovery cache in `router.Client` leaks one runner-config's `ServerURL`/`TLSData` to unrelated runner configs sharing the same process - ([File: router/client.go])

### Summary
`router.Client` is instantiated exactly once per `gitlab-runner run` process and is shared as the single `common.Network` implementation for every `[[runners]]` entry configured on that instance [1](#0-0) . Its discovery cache (`c.disco`, `c.discoExpiresAt`) is a single unkeyed pair of fields protected by one mutex, not a per-`RunnerConfig` (or per-GitLab-URL) cache, so the first `RunnerConfig` to populate it determines the `ServerURL`/`TLSData` used by every other `RunnerConfig` for up to `discoveryTTL` (1 hour) [2](#0-1) [3](#0-2) .

### Finding Description
`getRouterDiscovery` checks a single `discoExpiresAt`/`disco` pair under `c.mu`, and if not expired returns the cached value regardless of which `RunnerConfig` is asking: [3](#0-2) 

`RequestJob` calls `c.getRouterDiscovery(ctx, config)` for whichever `config` happens to be passed by the caller, then dials `disco.ServerURL` and eventually stamps the returned job with `disco.TLSData`: [4](#0-3) [5](#0-4) [6](#0-5) 

The `Client` instance is process-global: `main.go`'s `newClient` builds one `router.Client` wrapping one `network.GitLabClient` and hands it to `commands.NewRunCommand` as the single `common.Network` used for the whole runner fleet [1](#0-0) . `commands/multi.go` runs multiple concurrent worker goroutines, each potentially bound to a *different* `RunnerConfig` (different project, token, or even different GitLab instance URL), all calling `mr.network.RequestJob` on the same shared instance [7](#0-6) [8](#0-7) .

Because the cache key is nothing (a bare struct field), not `config.URL`/`config.Token`/project ID, whichever `RunnerConfig` first causes a cache miss populates `c.disco` for **every** subsequently-served `RunnerConfig` for the next hour — even runner configs pointing at a completely different GitLab instance. This is not merely a narrow TOCTOU race at line 244; it is the intended, but incorrect, caching design: there is no per-config partitioning at all.

Existing protections don't stop this: there is no scoping by config identity anywhere in `Client`, `getRouterDiscovery`, or `invalidateRouterDiscovery` [9](#0-8) , and the breaker/metrics logic operates on the same single shared state.

### Impact Explanation
A runner process configured with multiple `[[runners]]` entries (a very common, documented, legitimate multi-tenant configuration — different projects, different tokens, potentially different GitLab instances) will route job requests for config B through the router `ServerURL`/`TLSData` that was discovered for config A. Config B's job request JSON (containing its token) is sent to A's discovered router endpoint, and the job payload returned is stamped with A's `TLSData`, which later feeds job/trace/artifact network calls made on behalf of B's job. This crosses the "runner/GitLab auth state and TLS material must not cross project or runner-config boundaries" invariant and can leak a token/job request to the wrong backend or attach the wrong TLS trust material to a job's subsequent calls.

### Likelihood Explanation
This triggers under a normal, non-malicious, common operator configuration: any `gitlab-runner run` process with `concurrent` > 1 and more than one `[[runners]]` entry with `feature_flags.FF_USE_JOB_ROUTER` enabled. No attacker privilege beyond controlling which project's runner is registered is required, and it is fully deterministic once two configs are served by the same process — it doesn't need a tight race window, since the cache is simply shared for the full `discoveryTTL` (1 hour).

### Recommendation
Key the discovery cache by an identifier derived from `RunnerConfig` (e.g. `config.URL` + `config.Token`/system ID), replacing the single `disco`/`discoExpiresAt` fields with a map guarded by the existing mutex, or maintain one `router.Client`-equivalent cache per `RunnerConfig` rather than one process-wide `Client`. `invalidateRouterDiscovery` and the breaker should be scoped the same way.

### Proof of Concept
Go unit test in `router/client_test.go`:
1. Build one `*router.Client` with a `Delegate` mock whose `GetRouterDiscovery` returns `{ServerURL: "grpc://A", TLSData: tlsA}` for `configA` (matched by `config.URL == "https://gitlab-a.example"`) and `{ServerURL: "grpc://B", TLSData: tlsB}` for `configB` (`config.URL == "https://gitlab-b.example"`).
2. Call `rc.getRouterDiscovery(ctx, configA)` first (cache miss, populates `c.disco` with A's discovery).
3. Immediately call `rc.getRouterDiscovery(ctx, configB)` before `discoveryTTL` elapses.
4. Assert: the second call returns `disco.ServerURL == "grpc://B"` and `disco.TLSData == tlsB`. As currently implemented, it instead returns A's cached `ServerURL`/`TLSData`, proving the leak.

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

**File:** router/client.go (L111-114)
```go
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

**File:** router/client.go (L222-237)
```go
func parseJobResponse(job *rpc.GetJobResponse, responseMD metadata.MD, disco *common.RouterDiscovery, requestCorrelationID string, config common.RunnerConfig) (*spec.Job, bool) {
	if len(job.JobResponse) == 0 {
		return nil, true
	}
	var response spec.Job
	if err := json.Unmarshal(job.JobResponse, &response); err != nil {
		config.Log().WithError(err).Error("json.Unmarshal()")
		return nil, false
	}
	response.TLSData = disco.TLSData
	if correlationIDs := responseMD[requestIDMetadataKey]; len(correlationIDs) > 0 {
		requestCorrelationID = correlationIDs[0]
	}
	response.JobRequestCorrelationID = requestCorrelationID
	return &response, true
}
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

**File:** router/client.go (L255-260)
```go
func (c *Client) invalidateRouterDiscovery() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.disco = nil
	c.discoExpiresAt = time.Time{}
}
```

**File:** commands/multi.go (L1043-1090)
```go
func (mr *RunCommand) startWorkers(startWorker chan int, stopWorker chan bool, runners chan *common.RunnerConfig) {
	for mr.stopSignal == nil {
		id := <-startWorker
		go mr.processRunners(id, stopWorker, runners)
	}
}

// processRunners is responsible for processing a Runner on a worker (when received
// a runner information sent to the channel by feedRunners) and for terminating the worker
// (when received an information on stoWorker chan - provided by updateWorkers)
func (mr *RunCommand) processRunners(id int, stopWorker chan bool, runners chan *common.RunnerConfig) {
	mr.log().
		WithField("worker", id).
		Debugln("Starting worker")

	mr.runnerWorkerSlotOperations.WithLabelValues(workerSlotOperationStarted).Inc()

	for mr.stopSignal == nil {
		select {
		case runner := <-runners:
			err := mr.processRunner(id, runner, runners)
			if err != nil {
				logger := mr.log().
					WithFields(logrus.Fields{
						"runner":      runner.ShortDescription(),
						"runner_name": runner.Name,
						"executor":    runner.Executor,
					}).WithError(err)

				l, failureType := loggerAndFailureTypeFromError(logger, err)
				l("Failed to process runner")
				mr.runnerWorkerProcessingFailure.
					WithLabelValues(failureType, runner.ShortDescription(), runner.Name, runner.GetSystemID()).
					Inc()
			}

		case <-stopWorker:
			mr.log().
				WithField("worker", id).
				Debugln("Stopping worker")

			mr.runnerWorkerSlotOperations.WithLabelValues(workerSlotOperationStopped).Inc()

			return
		}
	}
	<-stopWorker
}
```

**File:** commands/multi.go (L1441-1456)
```go
}

// requeueRunner feeds the runners channel in a non-blocking way. This replicates the
// behavior of feedRunners and speeds-up jobs handling. But if the channel is full, the
// method just exits without blocking.
func (mr *RunCommand) requeueRunner(runner *common.RunnerConfig, runners chan *common.RunnerConfig) {
	runnerLog := mr.log().WithField("runner", runner.ShortDescription()).WithField("runner_name", runner.Name)

	select {
	case runners <- runner:
		runnerLog.Debugln("Requeued the runner")

	default:
		runnerLog.Debugln("Failed to requeue the runner")
	}
}
```
