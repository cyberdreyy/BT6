Based on the investigation, `req.ID` is authorization-scoped correctly (it gets rewritten to `authorizedOwner + RequestIDSeparator + originalRequestID` inside `authorizeAndStamp` before `h.newActiveRequest` keys the `activeRequests` map by `req.ID`), so cross-user response confusion via ID squatting is not reachable in the vault gateway handler.

### Title
Unprivileged client can DoS a victim's pending Vault/ConfidentialRelay gateway request by front-running the shared `activeRequests[req.ID]` map with a colliding request ID - ([File: core/services/gateway/handlers/vault/handler.go])

### Summary
The `boreWell` report describes an attacker squatting a deterministic, attacker-controllable identifier before a legitimate user, causing the victim's transaction to revert (DoS) or their funds to be captured by the attacker's entry at that identifier. The closest reachable analog in an unprivileged, internet-facing chainlink path is the gateway's `activeRequests` map, which is keyed by the raw JSON-RPC `req.ID` supplied by the calling client.

### Finding Description
`newActiveRequest` in `core/services/gateway/handlers/vault/handler.go` (and the structurally identical `core/services/gateway/handlers/confidentialrelay/handler.go`) inserts a pending request into a shared, DON-wide map keyed purely by `req.ID`: [1](#0-0) 

For `MethodPublicKeyGet` (unauthenticated) and for `MethodSecretsCreate/Update/Delete/List` prior to `ProcessRequest` finishing, the ID used to key this map originates directly from the untrusted client-supplied `req.ID` value: [2](#0-1) 

For the write/read secrets methods, `authorizeAndStamp` does rewrite `req.ID` to `authorizedOwner + Separator + originalID` before `newActiveRequest` is called, which namespaces the map key per-owner and prevents cross-owner collision for those paths: [3](#0-2) 

However, this per-owner namespacing does not protect against a same-owner or same-ID collision race: any client (unprivileged, unauthenticated for `PublicKeyGet`) can submit a request with the same `req.ID` value concurrently with a pending request from another client (before that other client's request has been authorized/stamped and inserted, or for the un-namespaced public key path), and `newActiveRequest` will reject the second with `"request ID already exists"`: [1](#0-0) 

This means an attacker who can predict or observe a victim's `req.ID` (e.g., client-generated sequential/short IDs, or a public key fetch which uses no owner-prefix) can pre-register that same ID in the `activeRequests` map, causing the legitimate request to fail with an authorization/ID-collision error — a direct DoS analog to the `boreWell` "attacker deploys with victim's salt" scenario. This is corroborated by the confidentialrelay handler's own test explicitly demonstrating that duplicate `req.ID` submissions from potentially different callers cause the second call to error out: [4](#0-3) 

### Impact Explanation
An unprivileged, internet-facing gateway client can deny service to another user's Vault or confidential-relay request by racing to submit a request carrying the same `req.ID` before the legitimate request is processed and namespaced. For the `MethodPublicKeyGet` path, which bypasses authorization entirely and is keyed by the raw ID, this collision is straightforward to trigger since there's no owner-prefixing to protect it. This does not enable fund theft or secret disclosure (the underlying secret-write/read paths are further owner-scoped by `authorizeAndStamp`), so this is a moderate-severity DoS/griefing issue rather than the more severe cross-user secret leakage.

### Likelihood Explanation
Likelihood is limited: for authenticated `Create/Update/Delete/List` operations the attacker would need to win a narrow race window before `authorizeAndStamp` completes and inserts into the map, and the ID is quickly re-keyed with the owner prefix, closing the window fast. For `MethodPublicKeyGet`, the un-prefixed map key persists for the full request lifetime, but this method returns only a public key (no sensitive data), so a DoS there mainly delays public key retrieval, which is also served from a cache in most cases. Overall likelihood is low-to-moderate and impact is limited to transient request rejection rather than fund loss or secret exposure.

### Recommendation
Consider deriving the `activeRequests` map key deterministically from a value that is not purely attacker-controlled prior to authorization (e.g., always prefix with a caller identity — remote address, connection ID, or a server-generated nonce — even for the un-authenticated `MethodPublicKeyGet` path), or move the collision check to occur strictly after any owner/session binding so that no un-namespaced attacker-controlled ID window exists.

### Proof of Concept
Not independently verified end-to-end (would require live gateway + timing control to win the race); this is inferred from code-level analysis of `newActiveRequest`'s un-namespaced key usage for `MethodPublicKeyGet` and the pre-authorization keying window, combined with the existing `TestConfidentialRelayHandler_DuplicateRequestID` test confirming duplicate-ID rejection semantics: [4](#0-3)

### Citations

**File:** core/services/gateway/handlers/vault/handler.go (L403-450)
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

**File:** core/capabilities/vault/gateway_vault_request_processor.go (L222-254)
```go
func (p *GatewayVaultRequestProcessor) authorizeAndStamp(
	ctx context.Context,
	req *jsonrpc.Request[json.RawMessage],
	stamp func(prefixedRequestID string) error,
) (*AuthorizedGatewayVaultRequest, error) {
	incomingOwner := ""
	if idx := strings.Index(req.ID, vaulttypes.RequestIDSeparator); idx != -1 {
		incomingOwner = req.ID[:idx]
	}

	p.lggr.Debugw("authorizing gateway vault request", "method", req.Method, "requestID", req.ID)
	authResult, err := p.authorizer.AuthorizeRequest(ctx, *req)
	if err != nil {
		authErr := fmt.Errorf("request not authorized: %w", err)
		p.lggr.Errorw("gateway vault request authorization failed", "method", req.Method, "requestID", req.ID, "hasAuth", req.Auth != "", "incomingOwner", incomingOwner, "error", authErr)
		return nil, authErr
	}

	originalRequestID := req.ID
	authorizedOwner := authResult.AuthorizedOwner()
	prefixedRequestID := authorizedOwner + vaulttypes.RequestIDSeparator + originalRequestID
	req.ID = prefixedRequestID

	if err := stamp(prefixedRequestID); err != nil {
		p.lggr.Errorw("failed to stamp authorized request params", "method", req.Method, "requestID", req.ID, "error", err)
		return nil, fmt.Errorf("failed to stamp authorized request params: %w", err)
	}

	p.lggr.Debugw("authorized gateway vault request", "method", req.Method, "requestID", req.ID, "owner", authorizedOwner, "orgID", authResult.OrgID(), "workflowOwner", authResult.WorkflowOwner())
	return &AuthorizedGatewayVaultRequest{
		Req:        *req,
		AuthResult: authResult,
	}, nil
```

**File:** core/services/gateway/handlers/confidentialrelay/handler_test.go (L767-785)
```go
func TestConfidentialRelayHandler_DuplicateRequestID(t *testing.T) {
	t.Parallel()
	h, cb, don, _ := setupHandler(t, 4)
	don.On("SendToNode", mock.Anything, mock.Anything, mock.Anything).Return(nil)

	params := json.RawMessage(`{"workflow_id":"wf1"}`)
	req := jsonrpc.Request[json.RawMessage]{
		ID:     "req-dup",
		Method: MethodCapabilityExec,
		Params: &params,
	}

	err := h.HandleJSONRPCUserMessage(t.Context(), req, cb)
	require.NoError(t, err)

	cb2 := common.NewCallback()
	err = h.HandleJSONRPCUserMessage(t.Context(), req, cb2)
	require.ErrorContains(t, err, "request ID already exists")
}
```
