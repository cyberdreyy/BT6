This confirms it: every config reload sets a fresh `ConfigLoadedAt = time.Now()` on all runner configs [1](#0-0) , and GitLab Runner reloads config periodically by design (not an attacker action, but routine operation) [2](#0-1) .

### Title
Pause-pod `activeJobs` counter desynchronization on config reload causes wrong autoscaler capacity decisions - (File: `executors/kubernetes/autoscaler/provider.go`)

### Summary
`Provider.Acquire()` and `Provider.Release()` independently look up the `PausePodManager` for a runner token via `GetManager()` and call `IncrementActiveJobs()` / `DecrementActiveJobs()` on whatever manager instance currently exists — without pinning the operation to the manager instance that was actually used at acquire time. When `ensureManager()` detects a config reload and swaps in a new `PausePodManager` for the same runner token, in-flight jobs that were counted on the old manager get decremented on the new manager instead, exactly mirroring the FrankenDAO bug where `stake()` and `unstake()` independently recompute a value from mutable global parameters instead of storing and reusing the original value recorded at stake time.

### Finding Description
`Provider.Acquire()` calls `ensureManager(config)`, then looks up the manager and increments its `activeJobs` counter [3](#0-2) . `ensureManager()` keys managers by runner token only, and if `config.ConfigLoadedAt` (the `configLoadedKey`) differs from what's stored, it stops the old manager and replaces `p.managers[token]` with a brand-new `PausePodManager` (with `activeJobs` reset to 0) [4](#0-3) .

`Provider.Release()` does not call `ensureManager()` and does not verify it is decrementing the same manager instance that was incremented at `Acquire()` time — it simply calls `GetManager(config)`, which returns whatever manager is currently registered for that token [5](#0-4) [6](#0-5) .

If a config reload happens while a job is running (a normal, periodic, admin-driven event that sets `ConfigLoadedAt = time.Now()` on every reload [1](#0-0) ), and any subsequent `Acquire()` call for the same runner token triggers `ensureManager()` to swap managers, then:
- The old manager is stopped (`Stop()` deletes its pause-pod deployment) while still logically "owning" `activeJobs=1` for the in-flight job [7](#0-6) .
- When the in-flight job eventually finishes, `Release()` calls `DecrementActiveJobs()` on the *new* manager instance, decrementing a counter that was never incremented for that job [5](#0-4) .

This directly parallels the report's root cause: two operations meant to be symmetric (`stake`/`unstake`, here `IncrementActiveJobs`/`DecrementActiveJobs`) each independently resolve a value/object from current mutable state (`getTokenVotingPower()` recomputation vs. `GetManager()` re-lookup) instead of using the value/handle captured at the first operation.

### Impact Explanation
The `activeJobs` count feeds `calculateDesiredReplicas()`, which drives `ScaleFactor`-based scale-up of pause pods used to pre-warm cluster capacity [8](#0-7) . An undercounted `activeJobs` (because a decrement landed on the wrong/new manager, or because the old manager's non-zero count is silently discarded when it's stopped) causes the reconciler to under-provision pause pods relative to real load, degrading the autoscaler's ability to keep capacity warm — a functional/availability degradation (persistent capacity-planning drift) rather than data corruption, since `DecrementActiveJobs()` is guarded against going negative [9](#0-8) .

### Likelihood Explanation
This requires no attacker action and no privileged/trusted-role compromise: it is triggered purely by GitLab Runner's normal periodic config reload combined with in-flight jobs, both routine parts of production operation. However, likelihood of the manager actually being *replaced* mid-job depends on whether `ConfigLoadedAt` changes between two `Acquire()` calls for the same token while a job is still running — this is plausible on any runner using the Kubernetes autoscaler executor with frequent config reload/checks, but I could not fully verify from the index how frequently `configfile.Load()` is invoked at runtime (e.g., the reload trigger/interval in `commands/multi.go`) to state a precise likelihood; a Devin agent with full source access should confirm the reload cadence to firm this up.

### Recommendation
Have `Acquire()` capture the specific `*PausePodManager` pointer used for the increment (e.g., return it or store it on the `common.ExecutorData`/build context), and have `Release()` decrement that same captured instance rather than re-resolving it via `GetManager(config)` at release time — analogous to the audit's recommendation to persist the originally-computed value (`tokenVotingPower`) instead of recomputing it from possibly-changed global state.

### Proof of Concept
1. Configure a runner with `kubernetes.autoscaler` policy enabled.
2. Start a job — `Provider.Acquire()` creates `manager1` for the runner token and increments `manager1.activeJobs` to 1 [3](#0-2) .
3. While the job is still running, trigger a config reload that changes `ConfigLoadedAt` for that runner (this happens automatically on every reload [1](#0-0) ), and issue another `Acquire()` for the same runner token (e.g., a second concurrent job) — `ensureManager()` stops `manager1` and installs `manager2` with `activeJobs=0` [10](#0-9) .
4. When the first job finishes, `Provider.Release()` calls `GetManager(config)` → returns `manager2`, and decrements `manager2.activeJobs` (which was never incremented for this job) [5](#0-4) .
5. Result: `manager1` (now stopped) is discarded with a stale `activeJobs=1` it never got to decrement, and `manager2`'s count is now inconsistent with the real number of active jobs, matching `TestProvider_ConfigReload_ReplacesManager`'s scenario of manager replacement without addressing counter continuity [11](#0-10) .

### Citations

**File:** commands/internal/configfile/configfile.go (L85-90)
```go
	cf.cfg = config
	for _, runnerCfg := range cf.cfg.Runners {
		runnerCfg.SystemID = cf.systemID
		runnerCfg.ConfigLoadedAt = time.Now()
		runnerCfg.ConfigDir = filepath.Dir(cf.pathname)
	}
```

**File:** commands/multi.go (L1464-1466)
```go
func (mr *RunCommand) updateWorkers(workerIndex *int, startWorker chan int, stopWorker chan bool) os.Signal {
	config := mr.configfile.Config()
	concurrentLimit := config.Concurrent
```

**File:** executors/kubernetes/autoscaler/provider.go (L82-97)
```go
func (p *Provider) Acquire(config *common.RunnerConfig) (common.ExecutorData, error) {
	if err := p.ensureManager(config); err != nil {
		logrus.WithError(err).Warn("Failed to start pause pod manager")
	}

	data, err := p.ExecutorProvider.Acquire(config)
	if err != nil {
		return data, err
	}

	if manager := p.GetManager(config); manager != nil {
		manager.IncrementActiveJobs()
	}

	return data, nil
}
```

**File:** executors/kubernetes/autoscaler/provider.go (L99-106)
```go
// Release releases resources and decrements the active job count.
func (p *Provider) Release(config *common.RunnerConfig, data common.ExecutorData) {
	if manager := p.GetManager(config); manager != nil {
		manager.DecrementActiveJobs()
	}

	p.ExecutorProvider.Release(config, data)
}
```

**File:** executors/kubernetes/autoscaler/provider.go (L108-149)
```go
func (p *Provider) ensureManager(config *common.RunnerConfig) error {
	if config.Kubernetes == nil || config.Kubernetes.Autoscaler == nil {
		return nil
	}

	if len(config.Kubernetes.Autoscaler.Policy) == 0 {
		return nil
	}

	p.mu.Lock()
	defer p.mu.Unlock()

	token := config.GetToken()
	rm, exists := p.managers[token]

	// Check if config changed
	configKey := configLoadedKey(config)
	if exists && rm.configLoadedAt == configKey {
		return nil
	}

	// Stop existing manager if config changed
	if exists {
		rm.manager.Stop(context.Background())
		rm.cancel()
		delete(p.managers, token)
	}

	// Create new manager
	manager, cancel, err := p.createManager(config)
	if err != nil {
		return err
	}

	p.managers[token] = &autoscalingManager{
		manager:        manager,
		cancel:         cancel,
		configLoadedAt: configKey,
	}

	return nil
}
```

**File:** executors/kubernetes/autoscaler/provider.go (L221-232)
```go
// GetManager returns the pause pod manager for a runner, if one exists.
// This is used by the executor to update active job counts.
func (p *Provider) GetManager(config *common.RunnerConfig) *PausePodManager {
	p.mu.Lock()
	defer p.mu.Unlock()

	rm, exists := p.managers[config.GetToken()]
	if !exists {
		return nil
	}
	return rm.manager
}
```

**File:** executors/kubernetes/autoscaler/pause_pod_manager.go (L148-163)
```go
func (m *PausePodManager) Stop(ctx context.Context) {
	m.mu.Lock()
	if m.stopped {
		m.mu.Unlock()
		return
	}
	m.stopped = true
	close(m.stopCh)
	m.mu.Unlock()

	// Clean up deployment on shutdown
	m.log.Info("Cleaning up pause pod deployment on shutdown")
	if err := m.deleteDeployment(ctx); err != nil {
		m.log.WithError(err).Warn("Failed to clean up pause pod deployment")
	}
}
```

**File:** executors/kubernetes/autoscaler/pause_pod_manager.go (L179-186)
```go
// DecrementActiveJobs decrements the active job count.
func (m *PausePodManager) DecrementActiveJobs() {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.activeJobs > 0 {
		m.activeJobs--
	}
}
```

**File:** executors/kubernetes/autoscaler/pause_pod_manager.go (L259-277)
```go
func (m *PausePodManager) calculateDesiredReplicas(policy Policy) int {
	desired := policy.IdleCount

	// Apply scale factor if configured
	if policy.ScaleFactor > 0 {
		scaled := int(math.Ceil(policy.ScaleFactor * float64(m.getActiveJobs())))
		if policy.ScaleFactorLimit > 0 {
			scaled = min(scaled, policy.ScaleFactorLimit)
		}
		desired = max(desired, scaled)
	}

	// Don't exceed max pods
	if m.config.MaxPausePods > 0 {
		desired = min(desired, m.config.MaxPausePods)
	}

	return desired
}
```

**File:** executors/kubernetes/autoscaler/provider_test.go (L100-146)
```go
func TestProvider_ConfigReload_ReplacesManager(t *testing.T) {
	mockProvider := common.NewMockExecutorProvider(t)
	mockProvider.EXPECT().Acquire(mock.Anything).Return(nil, nil)

	provider := NewProvider(mockProvider)

	provider.newKubeClient = func(*restclient.Config) (kubernetes.Interface, error) {
		return fake.NewClientset(), nil
	}
	provider.getKubeConfig = func(*common.KubernetesConfig) (*restclient.Config, error) {
		return &restclient.Config{}, nil
	}

	configTime1 := time.Now()
	config := &common.RunnerConfig{
		RunnerCredentials: common.RunnerCredentials{
			Token: "test-token",
		},
		RunnerSettings: common.RunnerSettings{
			Kubernetes: &common.KubernetesConfig{
				Namespace: "default",
				Autoscaler: &common.KubernetesAutoscalerConfig{
					Policy: []common.AutoscalerPolicyConfig{
						{
							IdleCount: 2,
							Periods:   []string{"* * * * *"},
						},
					},
				},
			},
		},
		ConfigLoadedAt: configTime1,
	}

	// First acquire creates a manager
	_, err := provider.Acquire(config)
	require.NoError(t, err)

	manager1 := provider.GetManager(config)
	require.NotNil(t, manager1)

	// Same config timestamp - should reuse same manager
	_, err = provider.Acquire(config)
	require.NoError(t, err)

	manager1Again := provider.GetManager(config)
	assert.Same(t, manager1, manager1Again, "same config should reuse manager")
```
