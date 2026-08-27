## Analysis

The Lido bug class is: **a permissionless/unauthenticated call can mutate or occupy shared state that a legitimate, authorized operation depends on, causing the legitimate operation to fail (griefing/DoS)**. The closest reachable analog in this repository is in the Vault gateway handler's shared, ID-keyed in-flight request table, combined with the fact that one Vault method (`vault.publicKey.get`) is explicitly unauthenticated.

### Title
Unauthenticated `vault.publicKey.get` requests can grief in-flight authorized Vault requests via `activeRequests` ID collision - (File: `core/services/gateway/handlers/vault/handler.go`)

### Summary
The gateway-side Vault `handler` tracks in-flight requests in a single map, `activeRequests`, keyed only by the caller-supplied JSON-RPC `id` field, shared across *all* Vault methods and *all* callers. `vault.publicKey.get` is explicitly exempted from authorization [1](#0-0) , so any unauthenticated client can submit it. Because `newActiveRequest` rejects an incoming request outright if its `id` is already present in the map [2](#0-1) , an attacker who can predict or learn a request `id` that a legitimate (authorized) request will use can pre-occupy that slot with a cheap, unauthenticated `publicKey.get` call, causing the legitimate `secrets.create/update/delete/list` request with that same `id` to be rejected with `"request ID already exists"`.

### Finding Description
`HandleJSONRPCUserMessage` special-cases `MethodPublicKeyGet` to skip the authorization/`requestProcessor.ProcessRequest` step entirely ("Public key requests don't require authorization") and immediately calls `h.newActiveRequest(req, callback)` [3](#0-2) . For all other (authorized) Vault methods, `newActiveRequest` is also the single choke point used right after authorization succeeds [4](#0-3) .

`newActiveRequest` uses only `req.ID` as the map key, with no scoping by method, owner, or org:
```go
func (h *handler) newActiveRequest(req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) (*activeRequest, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.activeRequests[req.ID] != nil {
		h.lggr.Errorw("request id already exists", "requestID", req.ID)
		return nil, errors.New("request ID already exists: " + req.ID)
	}
	...
}
``` [2](#0-1) 

This is directly analogous to the reported bug class: `CSModule.depositETH()` is permissionless and mutates state (`bond`) that `addValidatorKeysETH()`'s precondition depends on, causing the legitimate call to revert. Here, `vault.publicKey.get` is permissionless and mutates a shared keyspace (`activeRequests`) that a legitimate, authorized `secrets.*` request's admission check depends on, causing the legitimate call to be rejected.

The entry point is fully internet/unprivileged-reachable: requests arrive from `gateway.ProcessRequest` (decoded straight from the raw HTTP body with no prior authentication) [5](#0-4)  and are routed to `handler.HandleJSONRPCUserMessage` without any gate for `MethodPublicKeyGet`.

### Impact Explanation
An attacker who can guess or observe a to-be-submitted request `id` (e.g., IDs following a predictable `method/workflowID/uuid` convention seen elsewhere in the gateway [6](#0-5) , or IDs that are otherwise not fully unpredictable to the attacker) can pre-empt that ID with a cheap, unauthenticated `publicKey.get` call. The legitimate, authorized `secrets.create/update/delete/list` request using the same ID will then be rejected with `"request ID already exists"`, denying the owner's workflow the ability to create/read/update/delete their secrets for that request attempt — a denial-of-service against a specific, targeted operation using only unauthenticated requests.

### Likelihood Explanation
The likelihood depends on the attacker's ability to predict/learn the exact `id` value used by the victim's request ahead of time. In the general case, request IDs generated as random UUIDs would defeat this attack, but the shared, unscoped map plus the unauthenticated exemption for `publicKey.get` still represents a violation of the isolation the JSON-RPC `id` mapping is supposed to provide, and any weakening or predictability in ID generation on the client side (as seen with the `method/workflowID/uuid` naming convention used in other gateway v2 handlers) would make this practically exploitable. There is no per-owner, per-method, or per-org namespacing in `activeRequests` to prevent cross-tenant/cross-method interference even for benign collisions.

### Recommendation
- Scope the `activeRequests` map key by `(method, id)` or by `(authorizedOwner, id)` instead of `id` alone, so an unauthenticated `publicKey.get` request cannot collide with an authorized `secrets.*` request.
- Require the "request ID already exists" check to happen only for identical `(method, id)` tuples, or generate an internal collision-resistant key server-side rather than trusting the client-supplied `id` for uniqueness guarantees across trust boundaries.
- Consider requiring at least a minimal authentication/allowlist step for `publicKey.get` as well, or explicitly isolate its ID namespace from the authorized methods' namespace.

### Proof of Concept
1. Attacker predicts/learns the JSON-RPC `id` that a victim's SDK will use for an upcoming `vault.secrets.create` request (e.g., due to a predictable ID scheme).
2. Attacker sends an unauthenticated `vault.publicKey.get` request to the gateway HTTP endpoint with that same `id` value. Because `MethodPublicKeyGet` skips authorization, `newActiveRequest` inserts the ID into `activeRequests`.
3. Victim's legitimate, properly-authorized `vault.secrets.create` request with the same `id` arrives shortly after; `newActiveRequest` sees the ID already present and returns `"request ID already exists"`, causing `HandleJSONRPCUserMessage` to fail the victim's request [7](#0-6) .

### Citations

**File:** core/services/gateway/handlers/vault/handler.go (L403-429)
```go
func (h *handler) HandleJSONRPCUserMessage(ctx context.Context, req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) error {
	if req.ID == "" {
		return errors.New("request ID cannot be empty")
	}
	if len(req.ID) > 200 {
		// Arbitrary limit to prevent abuse
		return errors.New("request ID is too long: " + strconv.Itoa(len(req.ID)) + ". max is 200 characters")
	}

	h.lggr.Debugw("handling vault request", "method", req.Method, "requestID", req.ID, "request", req)
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
	}
```

**File:** core/services/gateway/handlers/vault/handler.go (L436-450)
```go
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

**File:** core/services/gateway/gateway.go (L217-230)
```go
// Called by the server
func (g *gateway) ProcessRequest(ctx context.Context, rawRequest []byte, auth string) (rawResponse []byte, httpStatusCode int) {
	// decode
	jsonRequest, err := jsonrpc2.DecodeRequest[json.RawMessage](rawRequest, auth)
	if err != nil {
		return newError("", api.UserMessageParseError, err.Error())
	}
	msg, err := g.codec.DecodeJSONRequest(jsonRequest)
	if err != nil {
		return newError(jsonRequest.ID, api.UserMessageParseError, err.Error())
	}
	if len(jsonRequest.ID) > 200 {
		// Arbitrary limit to prevent abuse
		return newError(jsonRequest.ID, api.UserMessageParseError, "request ID is too long: "+strconv.Itoa(len(jsonRequest.ID))+". max is 200 characters")
```

**File:** core/services/gateway/handlers/capabilities/v2/http_handler_test.go (L875-876)
```go
	id := gateway_common.MethodHTTPAction + "/workflowId123/uuid456"
	rawRequest := json.RawMessage(reqBytes)
```
