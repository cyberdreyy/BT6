### Title
Unauthenticated Pipeline-Run Resume Endpoint Allows Capture-and-Replay Result Injection - (File: core/web/pipeline_runs_controller.go)

### Summary
The `PATCH /v2/resume/:runID` endpoint, handled by `PipelineRunsController.Resume`, accepts a bare UUID (`taskID`) as its only "credential" and is deliberately excluded from the standard session/API-key/external-initiator authentication chain used elsewhere in `core/web/auth/auth.go`. Anyone who captures this UUID (e.g., from EA/bridge server logs, proxy logs, browser history, referer headers, or network capture) can submit or replay a result to that token indefinitely to control the outcome of a suspended pipeline run. This mirrors the CWE class in the reference report (authentication bypass by capture-replay) — a single, non-expiring, non-audience-bound token stands in for authentication, and unlike other sensitive request paths in this codebase, there is no replay guard on it.

### Finding Description
`Resume` parses `runID` as a UUID and calls `prc.App.ResumeJobV2(ctx, taskID, result)` with no session, API-token, or external-initiator check: [1](#0-0) 

The audit log entry explicitly documents this as unauthenticated: `audit.UnauthedRunResumed`. [2](#0-1) 

`ResumeJobV2` forwards directly into `pipeline.Runner.ResumeRun`, which looks up the pending task purely by `taskID` and immediately applies the caller-supplied `value`/`error` to resume the paused job, with no freshness, single-use, or ownership check on the UUID itself: [3](#0-2) 

The UUID is the async-bridge resume token, embedded verbatim in outbound bridge/external-adapter requests as `responseURL`: [4](#0-3) 

By design this endpoint trusts "possession of the URL" as its authentication method — the same trust model (a bearer secret transmitted in a request/URL, with no nonce consumption or single-use enforcement at the authorization layer) that the ethermint CVE exploited via a large, un-consumed nonce enabling transaction replay. Contrast this with other capture-sensitive flows in this same codebase that *do* defend against exactly this bug class:
- Vault JSON-RPC requests use a `RequestReplayGuard` that rejects a previously-seen (digest, expiry) pair: [5](#0-4) 
- Gateway HTTP-trigger JWTs are checked against a `jwtReplayCache` keyed by `jti`, explicitly to "prevent replay attacks": [6](#0-5) [7](#0-6) 

The `/v2/resume/:runID` path has no equivalent protection — no token expiry, no single-use enforcement, no bearer secret, no signature — it relies solely on UUID unguessability and does not treat capture (leak) of the UUID as a compromised credential requiring replay defenses.

### Impact Explanation
If a resume-token UUID leaks (a realistic and commonly-cited risk for async EA callback URLs — proxy/access logs, browser extensions, external-adapter operator logs, TLS termination logs, Referer headers if the EA makes further outbound calls, etc.), an unprivileged attacker can:
- Repeatedly replay a captured resume request to reset/alter the outcome of a still-pending run before it completes, or resubmit stale data.
- Inject arbitrary attacker-controlled result values/errors into a paused pipeline run, which for `directrequest`/`ethtx`-linked jobs can influence on-chain fulfillment data submitted by the node (see `DirectRequestTxPipelineSpec`, where a bridge/parse result feeds directly into an `ethabiencode`/`submit ethtx` step): [8](#0-7) 

This is an "unauthorized job run" / "fund movement" class impact per the validation criteria: result-poisoning of a pending run whose output feeds an on-chain transaction.

### Likelihood Explanation
Exploitation requires the attacker to first obtain the resume UUID, so likelihood is lower than a pure unauthenticated endpoint, but non-trivial: the URL is transmitted over the network to third-party external adapters/bridges as plaintext in JSON POST bodies, and there is no rate limiting, single-use consumption, or expiry visible at this layer — so once captured, the token remains a durable, freely replayable credential. This matches "capture-replay" exactly, as opposed to a brute-force scenario.

### Recommendation
- Bind the resume token to a single use (mark the task run "consumed" atomically on first successful resume, reject subsequent resumes for the same `taskID`) analogous to `RequestReplayGuard`/`jwtReplayCache` used elsewhere.
- Add an expiry to resume tokens consistent with the async task's configured timeout, and reject resumes for expired/already-finished task runs.
- Consider adding a per-run secret/HMAC additional to the UUID (defense in depth) so URL leakage alone is insufficient, and ensure this responseURL is never logged in plaintext by the node itself.
- Add audit-log correlation/anomaly detection for repeated resume attempts against the same `taskID`.

### Proof of Concept
1. Configure an async bridge task; the node sends an EA request containing `responseURL: http://<node>/v2/resume/<taskID>` per `finalizeAndMarshalBridgeRequestData` (`core/services/pipeline/task.bridge.go:363-373`).
2. An attacker who observes this URL (e.g., via EA-side logging, a compromised or malicious proxy in the request path, or shared infra logs) captures `<taskID>`.
3. Attacker sends `PATCH /v2/resume/<taskID>` with an arbitrary JSON body matching `pipeline.ResumeRequest` — no headers, cookies, or API keys required (confirmed by the absence of any `auth.Authenticate*` call in `Resume`, and by `audit.UnauthedRunResumed`).
4. The pending pipeline run resumes with attacker-supplied `value`/`error`, and can be replayed multiple times as long as the underlying task run remains unresolved, since no single-use/expiry check exists in `ResumeRun` (`core/services/pipeline/runner.go:734-757`).

### Citations

**File:** core/web/pipeline_runs_controller.go (L133-159)
```go
func (prc *PipelineRunsController) Resume(c *gin.Context) {
	taskID, err := uuid.Parse(c.Param("runID"))
	if err != nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, err)
		return
	}

	rr := pipeline.ResumeRequest{}
	decoder := json.NewDecoder(c.Request.Body)
	err = errors.Wrap(decoder.Decode(&rr), "failed to unmarshal JSON body")
	if err != nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, err)
		return
	}
	result, err := rr.ToResult()
	if err != nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, err)
		return
	}

	if err := prc.App.ResumeJobV2(c.Request.Context(), taskID, result); err != nil {
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}

	prc.App.GetAuditLogger().Audit(audit.UnauthedRunResumed, map[string]any{"runID": c.Param("runID")})
	c.Status(http.StatusOK)
```

**File:** core/services/pipeline/runner.go (L734-757)
```go
func (r *runner) ResumeRun(ctx context.Context, taskID uuid.UUID, value any, err error) error {
	run, start, err := r.orm.UpdateTaskRunResult(ctx, taskID, Result{
		Value: value,
		Error: err,
	})
	if err != nil {
		return fmt.Errorf("failed to update task run result: %w", err)
	}

	// TODO: Should probably replace this with a listener to update events
	// which allows to pass in a transactionalised database to this function
	if start {
		// start the runner again
		go func() {
			ctx, cancel := r.chStop.NewCtx()
			defer cancel()
			if _, err := r.Run(ctx, &run, false, nil); err != nil {
				r.lggr.Errorw("Resume run failure", "err", err)
			}
			r.lggr.Debug("Resume run success")
		}()
	}
	return nil
}
```

**File:** core/services/pipeline/task.bridge.go (L363-373)
```go
	if t.Async == "true" {
		responseURL := t.bridgeConfig.BridgeResponseURL()
		if responseURL != nil && *responseURL != *zeroURL {
			responseURL.Path = path.Join(responseURL.Path, "/v2/resume/", t.uuid.String())
		}
		var s string
		if responseURL != nil {
			s = responseURL.String()
		}
		merged["responseURL"] = s
	}
```

**File:** core/capabilities/vault/request_replay_guard.go (L35-47)
```go
func (g *RequestReplayGuard) CheckAndRecord(digest string, expiresAtUnix int64) error {
	g.mu.Lock()
	defer g.mu.Unlock()

	g.clearExpiredLocked()

	if _, exists := g.seen[digest]; exists {
		return ErrRequestAlreadySeen
	}

	g.seen[digest] = expiresAtUnix
	return nil
}
```

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L29-30)
```go
// jwtReplayCache manages used JWT IDs to prevent replay attacks
type jwtReplayCache struct {
```

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L399-412)
```go
func (cache *jwtReplayCache) isReplay(jti string) bool {
	cache.mu.RLock()
	defer cache.mu.RUnlock()

	_, exists := cache.cache[jti]
	return exists
}

func (cache *jwtReplayCache) recordUsage(jti string) {
	cache.mu.Lock()
	defer cache.mu.Unlock()

	cache.cache[jti] = time.Now()
}
```

**File:** deployment/environment/nodeclient/chainlink_models.go (L793-811)
```go
// String representation of the pipeline
func (d *DirectRequestTxPipelineSpec) String() (string, error) {
	sourceString := `
            decode_log   [type=ethabidecodelog
                         abi="OracleRequest(bytes32 indexed specId, address requester, bytes32 requestId, uint256 payment, address callbackAddr, bytes4 callbackFunctionId, uint256 cancelExpiration, uint256 dataVersion, bytes data)"
                         data="$(jobRun.logData)"
                         topics="$(jobRun.logTopics)"]
			encode_tx  [type=ethabiencode
                        abi="fulfill(bytes32 _requestId, uint256 _data)"
                        data=<{
                          "_requestId": $(decode_log.requestId),
                          "_data": $(parse)
                         }>
                       ]
			fetch  [type=bridge name="{{.BridgeTypeAttributes.Name}}" requestData="{{.BridgeTypeAttributes.RequestData}}"];
			parse  [type=jsonparse path="{{.DataPath}}"]
            submit [type=ethtx to="$(decode_log.requester)" data="$(encode_tx)" failOnRevert=true]
			decode_log -> fetch -> parse -> encode_tx -> submit`
	return MarshallTemplate(d, "Direct request pipeline template", sourceString)
```
