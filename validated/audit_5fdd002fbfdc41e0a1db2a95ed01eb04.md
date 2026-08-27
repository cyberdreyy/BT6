### Title
Denial of Service via Global (Non-Owner-Scoped) Request-ID Collision in Vault Gateway Handler - (File: core/services/gateway/handlers/vault/handler.go)

### Summary
The Vault gateway handler tracks in-flight secrets requests (`SecretsCreate`, `SecretsUpdate`, `SecretsDelete`, `SecretsList`) in a single process-wide map keyed **only** by the client-supplied JSON-RPC `req.ID`, with no binding to the authorized owner/org that issued the request. Any authenticated-but-unprivileged Vault user can therefore squat or race a request ID that collides with a different, unrelated user's in-flight request, causing that other user's legitimate secret create/update/delete operation to be rejected outright. This is the same root cause pattern as the external report's `merkleRoot`-as-`salt` collision: a value that should be scoped per-caller is instead used as a global uniqueness key, so an unprivileged party can deny a completely unrelated transaction just by colliding on that identifier.

### Finding Description
In `newActiveRequest`, the handler checks and inserts into `h.activeRequests` using only `req.ID`: [1](#0-0) 

This is invoked from `HandleJSONRPCUserMessage` right after the request passes authorization (`h.requestProcessor.ProcessRequest`), but the map key does not incorporate `authorizedOwner`, org, or sender identity — it is purely `req.ID`, a value fully controlled by the requesting client: [2](#0-1) 

Because `req.ID` (bounded only by a 200-character length check) is attacker-chosen and the uniqueness check is global across all owners/organizations using the same gateway, an unprivileged Vault client can:
1. Submit (or repeatedly submit/race) requests using common or predictable ID values (e.g., `"1"`, sequential counters, timestamps, or values inferred from another user's client behavior).
2. If another legitimate user's request happens to use the same `req.ID` while the attacker's is still in flight, `newActiveRequest` returns `"request ID already exists"`, which is surfaced back to the legitimate caller as an error and the legitimate secrets operation never reaches the DON.

The analogous `httpTriggerHandler.setupCallback` has the identical pattern — a single global `callbacks` map keyed only by `requestID`, unscoped to workflow/owner — which produces the same class of cross-tenant ID-squatting DoS on workflow HTTP trigger execution: [3](#0-2) 

By contrast, the shared `RequestCache` used elsewhere in the gateway correctly scopes uniqueness to `(sender, id)` rather than `id` alone, avoiding this cross-tenant collision: [4](#0-3) 

### Impact Explanation
An unprivileged (or low-privilege, differently-scoped) authenticated Vault/HTTP-trigger client can deny service to unrelated users' legitimate operations by colliding on a globally-unique request identifier that should have been scoped per caller/owner. For the Vault handler this can block secret creation, update, or deletion for a targeted owner/org — a security-relevant capability given Vault's role in secrets management — purely through ID collision, with no need to compromise the victim's authorization or keys.

### Likelihood Explanation
Exploitability depends on the attacker being able to predict or race a victim's `req.ID`. Since `req.ID` is entirely client-generated (no server-enforced randomness/uniqueness requirement, only a length bound), clients using simple/sequential/timestamp-based ID schemes are plausible in practice, and an attacker only needs authorization to call the Vault/HTTP-trigger methods for their *own* resources to trigger the collision against someone else's request — no cross-tenant authorization bypass is required to launch the DoS.

### Recommendation
Scope the in-flight request de-duplication key to the authorized identity in addition to the client-supplied ID — e.g., key `activeRequests`/`callbacks` by `(authorizedOwner, req.ID)` similar to the `(sender, id)` pattern already used in `handlerscommon.RequestCache`, so that request-ID collisions can only occur within a single owner's own requests rather than across unrelated tenants.

### Proof of Concept
1. Attacker authenticates as Owner A and is authorized to call `vaulttypes.MethodSecretsCreate`/`Update`/`Delete` for their own secrets.
2. Attacker sends a request with `req.ID = "1"` (or any ID pattern they anticipate Owner B's client will use) and keeps it artificially long-lived/pending (e.g., by not completing quorum, or simply timing many concurrent submissions).
3. Victim (Owner B), using the same gateway, sends a legitimate request that happens to reuse `req.ID = "1"`.
4. `newActiveRequest` in `core/services/gateway/handlers/vault/handler.go` finds `h.activeRequests["1"]` already populated (by Owner A's unrelated request) and returns `"request ID already exists: 1"`, causing Owner B's legitimate secrets operation to fail — despite Owner A having no authorization over Owner B's secrets.

### Citations

**File:** core/services/gateway/handlers/vault/handler.go (L431-450)
```go
	if !vaulttypes.IsGatewaySecretsMethod(req.Method) {
		return h.sendImmediateUserResponse(ctx, req, callback, api.UnsupportedMethodError, errors.New("this method is unsupported: "+req.Method))
	}

	_, cachedPublicKey := h.getCachedPublicKey()
	authorized, err := h.requestProcessor.ProcessRequest(ctx, &req, cachedPublicKey)
	if err != nil {
		if vaultcap.IsInvalidVaultParamsError(err) {
			return h.sendImmediateUserResponse(ctx, req, callback, api.InvalidParamsError, err)
		}
		h.lggr.Errorw("request not authorized", "method", req.Method, "requestID", req.ID, "hasAuth", req.Auth != "", "error", err)
		return errors.New("request not authorized: " + err.Error())
	}
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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L398-406)
```go
func (h *httpTriggerHandler) setupCallback(ctx context.Context, requestID string, callback handlers.Callback, requestStartTime time.Time, workflowID string) (<-chan struct{}, error) {
	h.callbacksMu.Lock()
	defer h.callbacksMu.Unlock()

	if _, found := h.callbacks[requestID]; found {
		h.handleUserError(ctx, requestID, jsonrpc.ErrConflict, fmt.Sprintf("requestID: %s has already been used. Ensure the requestID is unique for each request.", requestID), callback)
		return nil, fmt.Errorf("in-flight request ID: %s", requestID)
	}

```

**File:** core/services/gateway/handlers/common/requestcache.go (L34-63)
```go
type globalId struct {
	sender string
	id     string
}

type pendingRequest[T any] struct {
	handlers.Callback
	responseData *T
	timeoutTimer *time.Timer
	mu           sync.Mutex
}

func NewRequestCache[T any](timeout time.Duration, maxCacheSize uint32) RequestCache[T] {
	return &requestCache[T]{cache: make(map[globalId]*pendingRequest[T]), timeout: timeout, maxCacheSize: maxCacheSize}
}

func (c *requestCache[T]) NewRequest(lggr logger.Logger, request *api.Message, callback handlers.Callback, responseData *T) error {
	if request == nil {
		return errors.New("request is nil")
	}
	if responseData == nil {
		return errors.New("responseData is nil")
	}
	key := globalId{request.Body.Sender, request.Body.MessageId}
	c.mu.Lock()
	defer c.mu.Unlock()
	_, ok := c.cache[key]
	if ok {
		return errors.New("request already exists")
	}
```
