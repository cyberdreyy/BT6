### Title
Cross-Workflow HTTP Trigger Request-ID Collision Enables DOS of Legitimate Workflow Executions - (File: `core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go`)

### Summary
The gateway's HTTP trigger handler deduplicates and tracks in-flight requests using a map keyed solely by the client-supplied `req.ID` string, with no scoping to the workflow, workflow owner, or request payload. Because request IDs are freely chosen by any authorized caller for any registered workflow, an attacker who controls at least one workflow (or is any authorized signer) can pre-empt a specific request ID used by a different, unrelated workflow's legitimate client, causing that client's genuine trigger request to be rejected as a duplicate. This mirrors the reported bug class where a dedup/identity key omits the "important parameters" (here: workflow/tenant scope) needed to make the identifier collision-safe, enabling DOS by name/ID squatting.

### Finding Description
`httpTriggerHandler.callbacks` is declared as `map[string]savedCallback // requestID -> savedCallback` [1](#0-0) , a single global namespace shared by all workflows served by the gateway shard set.

`setupCallback` inserts and checks for collisions purely on this bare `requestID`: [2](#0-1) 

`validateRequestID` only rejects empty IDs or IDs containing `/`; it does not scope, hash, or namespace the ID by workflow, owner, or request content: [3](#0-2) 

Authorization (`authorizeRequest` → `WorkflowMetadataHandler.Authorize`) only verifies that the JWT signer is an authorized key *for the workflow the caller specifies*; it performs no check that the chosen `req.ID` is unique across other tenants/workflows: [4](#0-3) [5](#0-4) 

Because `checkRateLimit` and `setupCallback` are called sequentially and the request ID `req.ID` in `setupCallback` is checked/inserted only after authorization succeeds for the caller's own workflow, any authorized caller for Workflow A can submit a trigger request using a `req.ID` value they expect (guessed, predictable, or observed) to be used by Workflow B's client. If Workflow A's request lands first, `h.callbacks[requestID]` becomes occupied; when Workflow B's legitimate client then sends its real request with the same ID, it hits the duplicate check and is rejected with `jsonrpc.ErrConflict`: [6](#0-5) 

This is directly analogous to the Salty `Proposals.sol` bug: the identity used to prevent duplicates (`ballotName` there, `req.ID` here) does not incorporate all of the "important parameters" that make a request unique per actor (workflow/tenant), so an unprivileged party can squat on an identifier belonging to another party's legitimate operation and block it for the duration the identifier stays "in-flight" (until timeout via `reapExpiredCallbacks`, bounded by `CleanUpPeriodMs`) — and can repeat this indefinitely.

### Impact Explanation
An attacker who is merely an authorized signer of any workflow on the gateway (i.e., an unprivileged tenant, not an operator or node) can deny service to another tenant's HTTP-triggered workflow executions by colliding on the shared, unscoped `requestID` namespace. This is a request-impersonation/allowlist-scoping-bypass class issue: the per-workflow authorization boundary is effectively bypassed at the shared-state layer, letting one tenant's client-chosen identifier interfere with another tenant's callback registration and in turn prevent legitimate workflow runs from being tracked/responded to.

### Likelihood Explanation
Exploitation requires the attacker to predict or observe the victim's request ID. In practice this is often plausible: many client integrations use predictable IDs (sequence counters, timestamps, deterministic UUIDs derived from business data) rather than cryptographically random ones, and nothing in the gateway enforces or nudges towards randomness/scoping. Repeated squatting is cheap (only requires ownership of one authorized workflow and issuing valid JWT-signed requests), similar to the low-cost repeatable attack described in the source report.

### Recommendation
Scope the in-flight request tracking key to include the caller's own security context, e.g., use `(workflowID, requestID)` or `(authorizedOwnerPublicKey, requestID)` as the map key instead of bare `requestID`, mirroring the approach already used elsewhere in the codebase (e.g., the vault gateway processor prefixes `req.ID` with `authorizedOwner` before using it as a lookup key, and `RequestCache` keys on `{sender, id}` rather than `id` alone). This ensures a request ID collision can only occur within the same authorized workflow/owner, eliminating cross-tenant interference.

### Proof of Concept
1. Attacker registers/owns Workflow A and is an authorized signer for it.
2. Attacker observes or predicts that Workflow B's client will send `HandleUserTriggerRequest` with `req.ID = "X"` (e.g., a sequential/timestamp-based ID pattern).
3. Attacker sends a validly JWT-signed request for Workflow A with `req.ID = "X"`; `setupCallback` inserts `h.callbacks["X"]` [7](#0-6) .
4. Workflow B's legitimate client then sends its real request with `req.ID = "X"`; `setupCallback` finds the entry already present and returns `jsonrpc.ErrConflict` with "requestID ... has already been used" [6](#0-5) , denying the legitimate trigger.
5. Attacker repeats this for new predicted IDs after each entry is reaped/expired, sustaining the DOS.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L59-60)
```go
	callbacksMu             sync.Mutex
	callbacks               map[string]savedCallback // requestID -> savedCallback
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L183-195)
```go
func (h *httpTriggerHandler) validateRequestID(ctx context.Context, requestID string, callback handlers.Callback) error {
	if requestID == "" {
		h.handleUserError(ctx, requestID, jsonrpc.ErrInvalidRequest, "'id' field is required and cannot be empty. Use a new unique request 'id' for each request", callback)
		return errors.New("empty request ID")
	}
	// Request IDs from users must not contain "/", since this character is reserved
	// for internal node-to-node message routing (e.g., "http_action/{workflowID}/{uuid}").
	if strings.Contains(requestID, "/") {
		h.handleUserError(ctx, requestID, jsonrpc.ErrInvalidRequest, "request ID must not contain '/'", callback)
		return errors.New("request ID must not contain '/'")
	}
	return nil
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L361-369)
```go
func (h *httpTriggerHandler) authorizeRequest(ctx context.Context, workflowID string, req *jsonrpc.Request[json.RawMessage], callback handlers.Callback) (*gateway_common.AuthorizedKey, error) {
	h.lggr.Debugw("authorizing request", "workflowID", workflowID, "requestID", req.ID)
	key, err := h.workflowMetadataHandler.Authorize(workflowID, req.Auth, req)
	if err != nil {
		h.handleUserError(ctx, req.ID, jsonrpc.ErrInvalidRequest, "Auth failure: "+err.Error(), callback)
		return nil, errors.Join(errors.New("auth failure"), err)
	}
	return key, nil
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L398-405)
```go
func (h *httpTriggerHandler) setupCallback(ctx context.Context, requestID string, callback handlers.Callback, requestStartTime time.Time, workflowID string) (<-chan struct{}, error) {
	h.callbacksMu.Lock()
	defer h.callbacksMu.Unlock()

	if _, found := h.callbacks[requestID]; found {
		h.handleUserError(ctx, requestID, jsonrpc.ErrConflict, fmt.Sprintf("requestID: %s has already been used. Ensure the requestID is unique for each request.", requestID), callback)
		return nil, fmt.Errorf("in-flight request ID: %s", requestID)
	}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L426-434)
```go
	doneCh := make(chan struct{})
	h.callbacks[requestID] = savedCallback{
		Callback:            callback,
		requestStartTime:    requestStartTime,
		createdAt:           time.Now(),
		responseAggregators: aggregators,
		doneCh:              doneCh,
	}
	return doneCh, nil
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
