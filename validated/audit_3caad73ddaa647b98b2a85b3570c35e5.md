### Title
Unauthenticated `MethodPublicKeyGet` requests share the same `activeRequests` ID namespace as authorized/prefixed requests, allowing cross-owner request-ID collision - (File: core/services/gateway/handlers/vault/handler.go)

### Summary
`newActiveRequest` keys the single `h.activeRequests` map directly on `req.ID` with no separation between the unauthenticated `MethodPublicKeyGet` path (raw, client-supplied ID) and the authorized secrets methods path (owner-prefixed ID). An unauthenticated attacker can therefore submit a `MethodPublicKeyGet` request whose literal `req.ID` equals `<victimOwner><RequestIDSeparator><victimRequestID>` and collide with a legitimately prefixed victim request in the same map.

### Finding Description
For `MethodSecretsCreate/Update/Delete/List`, `HandleJSONRPCUserMessage` calls `h.requestProcessor.ProcessRequest`, which authorizes the request and then rewrites the ID as `authorizedOwner + vaulttypes.RequestIDSeparator + originalRequestID` before `h.newActiveRequest(req, callback)` is invoked [1](#0-0) . This prefixing is intended to give each owner an isolated ID namespace.

`MethodPublicKeyGet`, however, is explicitly documented as not requiring authorization and is dispatched *before* any prefixing occurs — `newActiveRequest` is called directly with the raw, client-controlled `req.ID` [2](#0-1) .

Both paths insert into the exact same map, keyed by the raw string `req.ID`, with no per-method or per-namespace partitioning: [3](#0-2) .

There is no validation rejecting `RequestIDSeparator` characters inside a client-supplied `req.ID` — the only checks in `HandleJSONRPCUserMessage` are non-empty and length ≤ 200 [4](#0-3) . Consequently, nothing stops an attacker from crafting a `MethodPublicKeyGet` request with `req.ID = "victimOwner" + RequestIDSeparator + "1"`.

Exploit flow:
1. Attacker (unauthenticated) sends `MethodPublicKeyGet` with `req.ID = "victimOwner:1"` while the cached public key is stale, so `newActiveRequest` is reached and registers that literal string as a map key.
2. Victim (authorized as owner `"victimOwner"`) submits a secrets request with client ID `"1"`. `ProcessRequest` authorizes it and rewrites `req.ID` to `"victimOwner:1"` — identical to the attacker's key.
3. `newActiveRequest` for the victim's request finds `h.activeRequests["victimOwner:1"] != nil` and returns `"request ID already exists"`, so `HandleJSONRPCUserMessage` returns that error without ever calling `callback.SendResponse`, meaning the victim's legitimate request is silently blocked/dropped for as long as the attacker's entry occupies the slot (until `removeExpiredRequests` clears it after `requestTimeout`, default 30s) [3](#0-2) .
4. Node responses are also routed via the same map key in `HandleNodeMessage` (`ar := h.getActiveRequest(resp.ID)`), so whichever request "owns" the colliding key is the only one that can ever be resolved — the loser's callback is never invoked through this code path [5](#0-4) .

The existing "duplicate requestId" unhappy-path test only demonstrates same-owner ID reuse being rejected; it does not cover the cross-namespace collision where an unauthenticated, unprefixed ID collides with an authorized, prefixed ID.

### Impact Explanation
This does not leak secrets or bypass authorization for the write/list vault methods — `newActiveRequest`'s uniqueness check does correctly prevent the colliding request from being registered, so the attacker cannot hijack the victim's response content (the map only ever holds one of the two entries at a time, and public-key data is not sensitive). The concrete impact is a **targeted denial-of-service / request-blocking primitive**: an unauthenticated party can pre-register a specific `owner:id` slot in the vault gateway handler's `activeRequests` map and prevent a legitimately authorized owner from placing a real (secrets create/update/delete/list) request under that exact ID for the duration of `requestTimeout`. This matches a low/DoS-class impact rather than authentication/authorization bypass or cross-user data disclosure, since it requires the attacker to predict both the victim's authorized owner string and the exact client-chosen request ID.

### Likelihood Explanation
Preconditions: attacker needs no credentials (the `MethodPublicKeyGet` path is intentionally unauthenticated) [6](#0-5) , but must know/guess the exact `authorizedOwner` string and the victim's client-chosen request ID, and must win the race to register before the victim's authorized request arrives, and the local public-key cache must be stale (`cachedPublicKey == nil`) for the code path to reach `newActiveRequest` at all — once cached, `MethodPublicKeyGet` is answered synchronously without touching the map [7](#0-6) . If victim request IDs are random UUIDs (as generated internally, e.g. `uuid.New().String()` in `fetchVaultPublicKey`), collision is practically infeasible; if any client uses predictable/sequential IDs, likelihood increases. Overall likelihood is low but the underlying design flaw (shared, unpartitioned ID namespace between authenticated and unauthenticated request classes) is real and reproducible.

### Recommendation
Partition the `activeRequests` map so unauthenticated `MethodPublicKeyGet` requests can never collide with owner-prefixed authorized requests — e.g., always prefix `MethodPublicKeyGet` IDs with a reserved, non-owner sentinel (or use a separate map for the public-key-get flow), and/or reject any incoming client `req.ID` containing `vaulttypes.RequestIDSeparator` before it reaches `newActiveRequest`.

### Proof of Concept
Go unit test in `core/services/gateway/handlers/vault/handler_test.go`:
1. Construct a `handler` with a stale (nil) public-key cache.
2. Call `newActiveRequest` (or drive it via `HandleJSONRPCUserMessage` with `req.Method = vaulttypes.MethodPublicKeyGet`) using `req.ID = "victimOwner" + vaulttypes.RequestIDSeparator + "1"` and a mock callback A.
3. Simulate the victim's authorized flow: build a request with `req.Method = vaulttypes.MethodSecretsList`, client ID `"1"`, mock the authorizer/`requestProcessor` to return `AuthorizedOwner() == "victimOwner"`, and call `HandleJSONRPCUserMessage` with mock callback B.
4. Assert step 3 returns the `"request ID already exists"` error from `newActiveRequest`, that callback B's `SendResponse`/`Wait` is never invoked with a success/aggregated result, and that only callback A's active request remains registered in `h.activeRequests`.
5. Additionally simulate a node response for `resp.ID = "victimOwner:1"` via `HandleNodeMessage` and assert it only ever resolves callback A, confirming the victim's request never receives a response through this collision (rather than the two callbacks being confused/swapped).

### Citations

**File:** core/capabilities/vault/gateway_vault_request_processor.go (L240-243)
```go
	originalRequestID := req.ID
	authorizedOwner := authResult.AuthorizedOwner()
	prefixedRequestID := authorizedOwner + vaulttypes.RequestIDSeparator + originalRequestID
	req.ID = prefixedRequestID
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

**File:** core/services/gateway/handlers/vault/handler.go (L413-428)
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
		h.lggr.Debugw("returning cached public key response")
		return h.handlePublicKeyGetSynchronously(ctx, req, publicKeyResponseBytes, callback)
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

**File:** core/services/gateway/handlers/vault/handler.go (L489-504)
```go
func (h *handler) HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	l := logger.With(h.lggr, "method", resp.Method, "requestID", resp.ID, "nodeAddr", nodeAddr)
	l.Debugw("handling node response")

	if !h.nodeRateLimiter.Allow(nodeAddr) {
		l.Debugw("node is rate limited", "nodeAddr", nodeAddr)
		return nil
	}

	ar := h.getActiveRequest(resp.ID)
	if ar == nil {
		// Request is not found, so we don't need to send a response to the user
		// This can happen if a slow node responds after the request has already been completed
		l.Debugw("no pending request found for ID")
		return nil
	}
```
