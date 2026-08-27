### Title
Attacker with any authorized workflow can frontrun a victim's HTTP trigger request by squatting a globally-shared `requestID`, causing the victim's legitimate execution to be rejected as a "conflict" - ([File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go])

### Summary
`httpTriggerHandler` deduplicates in-flight HTTP trigger requests using a single global map keyed only by the caller-supplied `requestID`, with no scoping by workflow ID or workflow owner. Any caller who is authorized to trigger *some* workflow (even a trivial one they control) can occupy an arbitrary `requestID` value in that shared map before a victim's request for a completely different workflow arrives, causing the victim's request to be rejected with a `Conflict` JSON-RPC error — mirroring the DYAD `idToBlockOfLastDeposit` griefing pattern, where an unprivileged/cheap action (own vault deposit / own workflow trigger) is used to poison shared keyed state that another user's legitimate operation depends on.

### Finding Description
`HandleUserTriggerRequest` validates and authorizes the request against the resolved `workflowID` [1](#0-0) , but the in-flight-request bookkeeping structure is declared as a single, gateway-wide map keyed purely by `requestID`, independent of workflow or owner: [2](#0-1) 

`setupCallback` enforces uniqueness only on this raw string key: [3](#0-2) 

Because `requestID` is entirely client-chosen (only validated for non-emptiness and absence of `/`, see `validateRequestID`) [4](#0-3) , and `authorizeRequest` only checks that the caller is authorized for *their own* `workflowID` [5](#0-4) , nothing prevents an attacker who controls or is authorized to trigger any workflow (including a trivial, cheap one of their own) from submitting a request whose `requestID` collides with a `requestID` value a victim is about to use (or has already reserved) for an unrelated workflow. Because the map is not namespaced by `workflowID`/owner, the second submitter — the victim — is rejected outright:

`"requestID: %s has already been used. Ensure the requestID is unique for each request."` (line 403).

This is directly analogous to the DYAD bug: the attacker doesn't need any privilege over the victim's resource (dNft/workflow) — they only need a cheap, unrelated, self-owned resource (a fake vault deposit / their own trivial workflow) to write into shared keyed state (`idToBlockOfLastDeposit[id]` / `h.callbacks[requestID]`) that a legitimate, unrelated user's operation checks for conflict/exclusivity before proceeding.

Notably, other gateway handlers in the same codebase treat this exact class of collision as a cross-tenant security concern and explicitly scope the `requestID` by the authorized owner before using it as a map key, e.g. the vault gateway request processor prefixes IDs with the authorized owner: [6](#0-5) 

(prefix pattern shown in `gateway_vault_request_processor.go`'s `authorizeAndStamp`, which computes `prefixedRequestID := authorizedOwner + vaulttypes.RequestIDSeparator + originalRequestID"` before using it as a lookup/response key — a mitigation absent from `httpTriggerHandler`).

### Impact Explanation
An unprivileged, unrelated caller can deny service to a specific victim's specific HTTP-triggered workflow execution by pre-claiming a `requestID` the victim is expected to use (e.g., idempotency keys, sequential/timestamp-based IDs, or IDs the attacker learns via other channels). The victim's legitimate call is rejected with a `Conflict` error and never dispatched to the DON, and the attacker can repeat this cheaply and indefinitely by triggering their own low-cost workflow with the colliding `requestID` each time the victim retries — a persistent, low-cost denial-of-service against a specific request/idempotency key, without needing any authorization over the victim's workflow.

### Likelihood Explanation
Exploitability depends on the attacker being able to predict or learn the victim's chosen `requestID` in advance (e.g., deterministic/idempotency-key based IDs are common client patterns), and on the attacker having authorization for any workflow at all (trivially satisfiable by registering/owning a cheap workflow). Given HTTP trigger `requestID`s are fully client-controlled strings with no per-caller namespace, the barrier to exploitation is low whenever request IDs are predictable, making this a realistic griefing vector for services relying on idempotency semantics.

### Recommendation
Scope the in-flight request map (and the "already used" conflict check) by a composite key that includes the resolved `workflowID` (and/or authorized owner), not just the raw client-supplied `requestID` — following the same pattern already used in `gateway_vault_request_processor.go`'s `authorizeAndStamp`, which prefixes the request ID with the authorized owner before using it as a lookup key. This ensures a `requestID` collision can only occur between requests belonging to the same, already-authorized workflow/owner, eliminating the cross-tenant griefing vector.

### Proof of Concept
1. Attacker registers/owns a trivial workflow `W_attacker` with a valid HTTP trigger and obtains authorization to call it.
2. Victim intends to call `workflows.execute` for `W_victim` using a predictable/idempotent `requestID = "order-42"`.
3. Attacker submits a `workflows.execute` request for `W_attacker` with `id = "order-42"` slightly before the victim's request arrives.
4. `setupCallback` inserts `h.callbacks["order-42"]` for the attacker's (unrelated) workflow.
5. Victim's genuine request for `W_victim` with `id = "order-42"` arrives; `setupCallback` finds the key already present and returns a `Conflict` (`jsonrpc.ErrConflict`) error, per lines 402–405 of `http_trigger_handler.go`, without ever forwarding the victim's request to their DON shard.
6. The attacker can repeat this for every retry attempt using the same idempotency key, at the trivial cost of triggering their own cheap workflow.

*Note: full confirmation that no additional per-owner scoping exists elsewhere in the request pipeline (e.g., in `WorkflowMetadataHandler.Authorize`) could not be completely verified due to tool budget limits on reading that file's full body; however, the `callbacks` map declaration and `setupCallback` logic shown above unambiguously key solely on the raw `requestID`.*

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L59-60)
```go
	callbacksMu             sync.Mutex
	callbacks               map[string]savedCallback // requestID -> savedCallback
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L88-106)
```go
func (h *httpTriggerHandler) HandleUserTriggerRequest(ctx context.Context, req *jsonrpc.Request[json.RawMessage], callback handlers.Callback, requestStartTime time.Time) error {
	triggerReq, err := h.validatedTriggerRequest(ctx, req, callback)
	if err != nil {
		return err
	}

	workflowID, err := h.resolveWorkflowID(ctx, triggerReq, req.ID, callback)
	if err != nil {
		return err
	}

	key, err := h.authorizeRequest(ctx, workflowID, req, callback)
	if err != nil {
		return err
	}

	if err = h.checkRateLimit(ctx, workflowID, req.ID, callback); err != nil {
		return err
	}
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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L240-243)
```go
	if hasWorkflowOwner {
		if err := h.validateWorkflowOwner(ctx, workflow.WorkflowOwner, requestID, callback); err != nil {
			return err
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
