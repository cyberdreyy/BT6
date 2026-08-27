### Title
Unbounded JWT replay-cache growth in Gateway HTTP Trigger authentication allows memory-exhaustion DoS - (File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go)

### Summary
The Gateway's `WorkflowMetadataHandler.Authorize` records every successfully-verified HTTP-trigger JWT into an in-memory map keyed by the JWT's `jti` claim, with no maximum size and no per-request-batch limit, and the map is only pruned by a periodic ticker that runs once per `JWTReplayPeriodMs` (default 24 hours). Because the JWT is signed client-side by a workflow's own already-authorized signer key, any registered (but otherwise unprivileged) workflow owner can mint an unbounded stream of distinct, validly-signed JWTs essentially for free and flood this cache before it is ever pruned, mirroring the "no minimum cost, unbounded low-value entries bloat storage" pattern from the reported Acala issue.

### Finding Description
`WorkflowMetadataHandler.Authorize` verifies the request JWT, checks replay via `h.jwtCache.isReplay(claims.ID)`, validates the signer against `authorizedKeys[workflowID]`, and then unconditionally calls `h.jwtCache.recordUsage(claims.ID)`, inserting a new map entry: [1](#0-0) 

The cache itself is a plain map with no capacity bound: [2](#0-1) [3](#0-2) 

Cleanup of expired entries is not size-triggered — it only runs on a ticker whose period equals `h.jwtCache.cleanupPeriod`, i.e. `JWTReplayPeriodMs` (default 24 hours): [4](#0-3) [5](#0-4) 

Per the documented HTTP Trigger message flow, JWT authentication (step 3, which writes to `jwtCache`) happens *before* per-workflow-owner rate limiting (step 4): [6](#0-5) 

Since the JWT is signed by the workflow's own private key (an action fully controlled by the workflow owner, who is an unprivileged client of the gateway, not a gateway operator), the owner can locally generate an unlimited number of distinct JWTs with unique `jti` values and unique nonce/expiry combinations, and submit them to the Gateway's HTTP Trigger endpoint. Each request that passes signature verification and the authorized-key check is accepted and adds a permanent (until the next 24h sweep) entry to the shared `jwtCache.cache` map — regardless of whether the request is ultimately rate-limited downstream in step 4. There is no minimum interval, no per-owner cap, and no maximum total cache size enforced at insertion time (`recordUsage`), analogous to the missing "minimum deposit" check in the reported `deposit_dex_share` bug.

### Impact Explanation
An attacker who controls (or compromises) any single registered workflow's signer key can grow the Gateway's in-memory `jwtCache` without bound for up to a full `JWTReplayPeriodMs` window (24 hours by default) before any pruning occurs. This is unbounded memory consumption on the Gateway process driven entirely by a legitimate, cheaply-repeatable client action (local JWT signing), potentially exhausting Gateway node memory and degrading or crashing the shared HTTP Trigger handling path for all DON workflows relying on that Gateway instance — a resource-exhaustion / availability impact consistent with the reported bug class ("Request/message patterns causing sustained crash or unbounded resource usage").

### Likelihood Explanation
Likelihood is moderate-to-high given: (1) generating unique, validly-signed JWTs is computationally cheap and requires no gateway-side interaction beyond the HTTP submission; (2) the only precondition is possessing one already-authorized workflow signer key, which is the normal operating condition for any workflow owner using the HTTP Trigger capability (not a privileged gateway role); (3) the vulnerable insertion path executes before any rate limiting is applied, so a naive rate limiter downstream does not prevent cache growth at the authentication step itself.

### Recommendation
Bound `jwtReplayCache` with an explicit maximum entry count (evicting oldest/expired entries eagerly on insert, e.g. via an LRU or a size check inside `recordUsage`), and/or enforce a per-workflow-owner or per-signer rate limit on JWT authentication attempts *before* `recordUsage` is called, rather than only after successful authentication in the downstream dispatch step. Consider triggering cleanup based on cache size thresholds in addition to the fixed time-based ticker.

### Proof of Concept
Conceptual PoC (cannot be executed without full gateway harness):
1. Register a workflow through the normal on-chain flow so its ECDSA signer key is present in `authorizedKeys[workflowID]`.
2. Using that signer key, locally generate N JSON-RPC requests, each with a fresh JSON-RPC `id`/payload and a freshly-signed JWT (`utils.CreateRequestJWT` + signing) containing a unique `jti` claim, per the pattern used in `TestWorkflowMetadataHandler_Authorize`'s "JWT replay protection" and "different JWT IDs should work" subtests: [7](#0-6) 
3. Submit all N requests to the Gateway's HTTP Trigger endpoint within a window shorter than `JWTReplayPeriodMs` (24h default). Each call to `Authorize` succeeds and calls `jwtCache.recordUsage(claims.ID)`, growing `jwtCache.cache` by one entry per request with no cap, until the next scheduled 24-hour cleanup tick.

Note: I was unable to fully trace whether the `httpTriggerHandler`'s per-workflow-owner rate limiter (mentioned in the README) is applied prior to or independent of the `Authorize` call at the wire-protocol level for every request type (e.g., whether it's checked before JWT verification for some code paths); this would need to be confirmed by inspecting `http_trigger_handler.go` in full, which I did not have remaining tool budget to review in detail.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L29-34)
```go
// jwtReplayCache manages used JWT IDs to prevent replay attacks
type jwtReplayCache struct {
	mu            sync.RWMutex
	cleanupPeriod time.Duration
	cache         map[string]time.Time // jti -> timestamp
}
```

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L80-108)
```go
func (h *WorkflowMetadataHandler) Authorize(workflowID string, token string, req *jsonrpc.Request[json.RawMessage]) (*gateway.AuthorizedKey, error) {
	claims, signer, err := utils.VerifyRequestJWT(token, *req)
	if err != nil {
		h.lggr.Errorw("Failed to verify JWT", "error", err)
		return nil, err
	}

	if h.jwtCache.isReplay(claims.ID) {
		h.lggr.Warnw("JWT token has already been used", "workflowID", workflowID, "signer", signer.Hex(), "jti", claims.ID)
		return nil, errors.New("JWT token has already been used. Please generate a new one with new id (jti)")
	}

	keys, exists := h.authorizedKeys[workflowID]
	if !exists {
		h.lggr.Errorw("Workflow ID not found in authorized keys", "workflowID", workflowID)
		return nil, fmt.Errorf("workflow ID %s not found", workflowID)
	}
	key := gateway.AuthorizedKey{
		KeyType:   gateway.KeyTypeECDSAEVM,
		PublicKey: strings.ToLower(signer.Hex()),
	}
	if _, exists = keys[key]; !exists {
		h.lggr.Errorw("Signer not found in authorized keys", "signer", signer.Hex())
		return nil, fmt.Errorf("signer '%s' is not authorized for workflow '%s'. Ensure that the signer is registered in the workflow definition", signer.Hex(), workflowID)
	}
	h.jwtCache.recordUsage(claims.ID)

	return &key, nil
}
```

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L298-304)
```go
		h.runTicker(h.jwtCache.cleanupPeriod, func(ctx context.Context) {
			now := time.Now()
			expiredCount := h.jwtCache.cleanupOldEntries(now.Add(-h.jwtCache.cleanupPeriod))
			h.metrics.IncrementJwtCacheCleanUpCount(ctx, int64(expiredCount), h.lggr)
			h.metrics.RecordJwtCacheSize(ctx, int64(len(h.jwtCache.cache)), h.lggr)
			h.lggr.Debugw("Workflow execution cache cleanup completed", "expired_entries", expiredCount, "remaining_entries", len(h.jwtCache.cache))
		})
```

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L392-412)
```go
func newJWTReplayCache(cleanupPeriod time.Duration) *jwtReplayCache {
	return &jwtReplayCache{
		cache:         make(map[string]time.Time),
		cleanupPeriod: cleanupPeriod,
	}
}

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

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L28-43)
```go
const (
	handlerName                          = "HTTPCapabilityHandler"
	defaultCleanUpPeriodMs               = 1000 * 60 * 10 // 10 minutes
	defaultMaxTriggerRequestDurationMs   = 1000 * 60      // 1 minute
	defaultNodeSendTimeoutMs             = 1000 * 10      // 10 seconds
	defaultInitialIntervalMs             = 100
	defaultMaxIntervalTimeMs             = 1000 * 30 // 30 seconds
	defaultMultiplier                    = 2.0
	defaultMetadataPullIntervalMs        = 1000 * 60 // 1 minute
	defaultMetadataAggregationIntervalMs = 1000 * 60 // 1 minute
	defaultMetadataPullRequestTimeoutMs  = 1000 * 30 // 30 seconds
	internalErrorMessage                 = "Internal server error occurred while processing the request"
	defaultOutboundRequestCacheTTLMs     = 1000 * 60 * 10      // 10 minutes
	defaultJWTReplayPeriodMs             = 1000 * 60 * 60 * 24 // 24 hours
	defaultSendResponseTimeoutMs         = 1000 * 5            // 5 seconds
)
```

**File:** core/services/gateway/handlers/capabilities/v2/README.md (L77-86)
```markdown
### 4.1 Process Flow

1. **Request Validation**: Validates JSON-RPC format, method, and parameters
2. **Workflow Resolution**: Resolves workflow ID from selector (ID, owner, name, tag)
3. **Authentication**: Verifies JWT token (ECDSA signature) and checks authorized keys
4. **Rate Limiting**: Enforces per-workflow-owner rate limits
5. **Node Distribution**: Sends request to all DON members with retry logic
6. **Response Aggregation**: Collects and aggregates responses from nodes (2f + 1 identical responses required, where f is max faulty nodes)
7. **User Response**: Returns aggregated result to the original requester

```

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler_test.go (L1193-1220)
```go
	t.Run("JWT replay protection", func(t *testing.T) {
		params := json.RawMessage(`{"test": "data"}`)
		req := &jsonrpc.Request[json.RawMessage]{
			Version: "2.0",
			ID:      "test-request-id-replay",
			Method:  gateway_common.MethodWorkflowExecute,
			Params:  &params,
		}

		token, err := utils.CreateRequestJWT(*req)
		require.NoError(t, err)

		tokenString, err := token.SignedString(privateKey)
		require.NoError(t, err)

		key, err := handler.Authorize(workflowID, tokenString, req)
		require.NoError(t, err)
		require.NotNil(t, key)

		// Second authorization with same JWT should fail (replay attack)
		key, err = handler.Authorize(workflowID, tokenString, req)
		require.Error(t, err)
		require.Contains(t, err.Error(), "JWT token has already been used. Please generate a new one with new id (jti)")
		require.Nil(t, key)
	})

	t.Run("different JWT IDs should work", func(t *testing.T) {
		params := json.RawMessage(`{"test": "data"}`)
```
