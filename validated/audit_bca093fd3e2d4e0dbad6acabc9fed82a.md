### Title
Unbounded `activeRequests` map growth via unique-ID flooding of `HandleJSONRPCUserMessage` — `nodeRateLimiter` only guards `HandleNodeMessage`, not user request ingestion - ([File: core/services/gateway/handlers/vault/handler.go])

### Summary
`h.nodeRateLimiter` in the Vault gateway handler is applied only in `HandleNodeMessage` keyed by `nodeAddr` [1](#0-0) , and has no role at all in `HandleJSONRPCUserMessage`/`newActiveRequest`. `newActiveRequest` inserts into the shared `h.activeRequests` map keyed only by `req.ID` with no per-caller quota and no cap on total map size, only rejecting exact duplicate IDs [2](#0-1) . This allows unbounded map growth from client-facing traffic that is completely decoupled from the node-facing rate limiter.

### Finding Description
`HandleJSONRPCUserMessage` performs only a length/emptiness check on `req.ID` (≤200 chars) before proceeding [3](#0-2) . For `vaulttypes.MethodPublicKeyGet`, no authorization is required at all — if the cached public key is unset (e.g., during startup, cache miss, or if quorum fails to complete), `newActiveRequest` is invoked directly with no auth check [4](#0-3) . For secrets methods, `newActiveRequest` is still called immediately after `ProcessRequest` authorization succeeds, but that authorization only validates signature/allowlist membership — it does not impose any quota on the number of concurrent or total requests a single authorized owner may register [5](#0-4) .

`newActiveRequest` itself has no capacity bound — it only rejects an exact duplicate `req.ID`, otherwise unconditionally inserts a new `*activeRequest` into `h.activeRequests` [2](#0-1) . Cleanup only happens on a 5-second ticker (`defaultCleanUpPeriod`) and only removes entries older than `requestTimeout` (default 30s) [6](#0-5) [7](#0-6) . Within that window, a caller sending unique `req.ID` values (up to 200 distinct ASCII characters, effectively unlimited combinations) faster than the 5s/30s cleanup cadence can insert an arbitrary number of entries, each holding a full request copy, a response map, and a mutex — with no ceiling.

`h.nodeRateLimiter.Allow(nodeAddr)` (configured via `Config.NodeRateLimiter`) is checked only inside `HandleNodeMessage`, which processes signed responses coming back from actual DON member nodes, keyed by their `nodeAddr` [1](#0-0) . This limiter cannot and does not throttle the rate at which new entries are created in `activeRequests` from the user-request path (`HandleJSONRPCUserMessage`/`newActiveRequest`), because those two code paths are entirely independent and keyed on different identities (`nodeAddr` vs `req.ID`).

### Impact Explanation
This is an unauthenticated/lightly-authenticated memory-exhaustion vector against the gateway process. `h.activeRequests` is a single map shared across all vault-handler traffic for a DON [8](#0-7) , guarded by a single `sync.Mutex` (`h.mu`) used for both reads (`getActiveRequest`) and writes (`newActiveRequest`), so an attacker growing this map both consumes memory and increases lock contention for all legitimate requesters and for `HandleNodeMessage`'s own bookkeeping. This matches a Denial-of-Service impact class (resource exhaustion / node-level DoS) rather than confidentiality/authorization bypass, since request IDs are still unique per request and there is no cross-user response confusion demonstrated.

Note: because `newActiveRequest` has no capacity check at all, legitimate requests are not "silently dropped" in favor of the attacker — both attacker and victim requests continue to be inserted successfully until the process runs out of memory. The precise "isolation" framing in the question (one user starving another's specific slot) does not hold as literally stated; the actual exploitable defect is unconditional, unbounded map growth leading to eventual resource exhaustion affecting all users of the gateway process/DON.

### Likelihood Explanation
For `MethodPublicKeyGet`, the path is reachable with zero authentication whenever the public key cache is not populated (startup, restart, cache miss window, or aggregation failures), making this trivially and repeatably triggerable pre-auth. For the secrets methods, the attacker must possess a valid signature accepted by the allowlist/JWT authorizer (i.e., be a legitimate but unprivileged workflow owner or holder of a signed gateway request, per the threat model) — an accepted "unprivileged" precondition under the audit rules. In either case, no additional cost/CAPTCHA/quota gates the number of distinct `req.ID`s a single caller can submit in a burst, so the attack is cheap and fully repeatable.

### Recommendation
Add an explicit bound on `h.activeRequests`, e.g., a global maximum concurrent-entry cap and/or a per-authorized-owner concurrency limiter (similar to `writeMethodsEnabled`/`limits.GateLimiter` already used elsewhere in this file), enforced inside `newActiveRequest` before insertion, returning a `LimitExceededError` when exceeded. Additionally, apply a per-caller (per authorized owner, or per anonymous source for `MethodPublicKeyGet`) rate limiter — distinct from `nodeRateLimiter` — on the ingress side of `HandleJSONRPCUserMessage`, and consider reducing `defaultCleanUpPeriod`/`requestTimeout` or actively evicting the oldest entries once a size threshold is reached.

### Proof of Concept
Go unit test plan in `core/services/gateway/handlers/vault/handler_test.go`:
1. Construct a `handler` with `newHandlerWithAuthorizer` (or `NewHandler`) using a test clock, no cached public key, and a `mockDon` whose `SendToNode` is a no-op/never resolves (so `newActiveRequest` entries are never cleaned up by successful response).
2. Loop calling `handler.HandleJSONRPCUserMessage(ctx, req, callback)` with `req.Method = vaulttypes.MethodPublicKeyGet` and a fresh UUID `req.ID` each iteration, N times (e.g., 100k) in a tight loop, well within the 5s cleanup tick.
3. Assert `len(handler.activeRequests)` grows unbounded/linearly with N (no rejection, no `LimitExceededError` returned) — confirming there is no cap.
4. In parallel, issue one "legitimate victim" request with its own unique ID and assert whether `newActiveRequest` latency increases materially or whether, under an added fix, it is rejected with an explicit `LimitExceededError` rather than exhausting memory silently.
5. Expected assertion for the fix: after remediation, once the map exceeds the configured cap, subsequent `newActiveRequest` calls return a distinct `LimitExceededError`-based response so that legitimate users get a clear, immediate signal rather than uncontrolled growth.

### Citations

**File:** core/services/gateway/handlers/vault/handler.go (L43-46)
```go
const (
	defaultCleanUpPeriod                    = 5 * time.Second
	defaultPublicKeyGetCacheDurationSeconds = 300
)
```

**File:** core/services/gateway/handlers/vault/handler.go (L152-153)
```go
	activeRequests                 map[string]*activeRequest
	metrics                        *metrics
```

**File:** core/services/gateway/handlers/vault/handler.go (L370-393)
```go
func (h *handler) removeExpiredRequests(ctx context.Context) {
	h.mu.RLock()
	var expiredRequests []*activeRequest
	now := h.clock.Now()
	for _, userRequest := range h.activeRequests {
		if now.Sub(userRequest.createdAt) > h.requestTimeout {
			expiredRequests = append(expiredRequests, userRequest)
		}
	}
	h.mu.RUnlock()

	for _, er := range expiredRequests {
		responses := er.copiedResponses()
		var nodeResponses strings.Builder
		for nodeKey, nodeResponse := range responses {
			_, _ = fmt.Fprintf(&nodeResponses, "%s ---::: %v               ", nodeKey, nodeResponse)
		}
		nodeResponsesStr := nodeResponses.String()
		err := h.sendResponse(ctx, er, h.errorResponse(er.req, api.RequestTimeoutError, errors.New("request expired without getting quorum of responses from nodes. Available responses: "+nodeResponsesStr), []byte(nodeResponsesStr)))
		if err != nil {
			h.lggr.Errorw("error sending response to user", "requestID", er.req.ID, "error", err)
		}
	}
}
```

**File:** core/services/gateway/handlers/vault/handler.go (L403-410)
```go
func (h *handler) HandleJSONRPCUserMessage(ctx context.Context, req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) error {
	if req.ID == "" {
		return errors.New("request ID cannot be empty")
	}
	if len(req.ID) > 200 {
		// Arbitrary limit to prevent abuse
		return errors.New("request ID is too long: " + strconv.Itoa(len(req.ID)) + ". max is 200 characters")
	}
```

**File:** core/services/gateway/handlers/vault/handler.go (L413-426)
```go
	if req.Method == vaulttypes.MethodPublicKeyGet {
		// Public key requests don't require authorization,
		// Let's process this request right away.
		// Note we cache this value quite aggressively so don't need to worry about DoS.
		publicKeyResponseBytes, cachedPublicKey := h.getCachedPublicKey()
		if cachedPublicKey == nil {
			// Not found in cache. Fetch from nodes.
			ar, err := h.newActiveRequest(req, callback)
			if err != nil {
				h.lggr.Errorw("failed to create new activeRequest", "error", err)
				return err
			}
			return h.handlePublicKeyGet(ctx, ar)
		}
```

**File:** core/services/gateway/handlers/vault/handler.go (L444-450)
```go
	authorizedOwner := authorized.AuthResult.AuthorizedOwner()

	h.lggr.Debugw("handling authorized vault request", "method", req.Method, "requestID", req.ID, "authorizedOwner", authorizedOwner)
	ar, activeRequestErr := h.newActiveRequest(req, callback)
	if activeRequestErr != nil {
		return activeRequestErr
	}
```

**File:** core/services/gateway/handlers/vault/handler.go (L466-481)
```go
func (h *handler) newActiveRequest(req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) (*activeRequest, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.activeRequests[req.ID] != nil {
		h.lggr.Errorw("request id already exists", "requestID", req.ID)
		return nil, errors.New("request ID already exists: " + req.ID)
	}
	ar := &activeRequest{
		Callback:  callback,
		req:       req,
		createdAt: h.clock.Now(),
		responses: map[string]*jsonrpc.Response[json.RawMessage]{},
	}
	h.activeRequests[req.ID] = ar
	return ar, nil
}
```

**File:** core/services/gateway/handlers/vault/handler.go (L489-496)
```go
func (h *handler) HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	l := logger.With(h.lggr, "method", resp.Method, "requestID", resp.ID, "nodeAddr", nodeAddr)
	l.Debugw("handling node response")

	if !h.nodeRateLimiter.Allow(nodeAddr) {
		l.Debugw("node is rate limited", "nodeAddr", nodeAddr)
		return nil
	}
```
