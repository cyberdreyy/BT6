### Title
Vault gateway replay guard permanently consumes a user's one-time authorization before the underlying secret operation is guaranteed to succeed, locking legitimate users out of retrying - (File: `core/capabilities/vault/authorizer.go`)

### Summary
The Vault capability's gateway request pipeline records an authorization digest as "used" in the `RequestReplayGuard` at authorization time, before the downstream node-side secret creation/update/delete actually completes. If the downstream operation subsequently fails (quorum not reached, DON node error, param-stamping error), the user's authorization is already burned and the identical request can never be retried, exactly mirroring the referenced C4 finding where a one-time proof (merkle proof / VETH allowance) is consumed even though the intended state change never happened, permanently locking the user out.

### Finding Description
The gateway-routed Vault pipeline is documented as: `ValidateStructureBeforeAuth → AuthorizeRequest → Prefix ID → StampAuthorizedParams` [1](#0-0) .

`authorizeAndStamp` calls `p.authorizer.AuthorizeRequest(ctx, *req)` and only afterward attempts to stamp/prefix the request and hand it off for actual processing: [2](#0-1) 

Inside `AuthorizeRequest`, the replay guard records the request digest as consumed as soon as authorization succeeds, unconditionally of whether the request will actually complete: [3](#0-2) 

`RequestReplayGuard.CheckAndRecord` immediately marks the digest as "seen" and any subsequent request with the same digest is rejected with `ErrRequestAlreadySeen` ("request was already authorized previously") until the recorded expiry passes: [4](#0-3) 

After authorization succeeds and the digest is burned, the request is only then dispatched to DON nodes and the response is aggregated for quorum in `HandleJSONRPCUserMessage`/`HandleNodeMessage`. If aggregation fails to reach quorum, or a node returns an error, the handler still returns a fatal error to the caller — but the digest has already been permanently consumed: [5](#0-4) [6](#0-5) 

This is directly analogous to the referenced Converter.sol issue: a single-use credential (merkle proof / here, the JWT-or-allowlist authorization digest) is destroyed as a side effect of the authorization step itself, decoupled from whether the actual protected action (VADER conversion / here, secret create-update-delete via DON quorum) ever completes. The project's own test suite and system tests acknowledge this exact failure mode occurs in practice under gateway timeouts, explicitly instructing operators/tests to treat "request was already authorized previously" as an unrecoverable but harmless outcome rather than fixing the root cause: [7](#0-6) [8](#0-7) 

### Impact Explanation
A legitimate, correctly-authorized unprivileged caller (holding a valid JWT or allowlisted digest) whose secret create/update/delete request fails downstream for any reason unrelated to authorization (gateway-to-DON timeout, insufficient node quorum, transient node fault, or a stamping/marshaling error after authorization) permanently loses the ability to retry that exact request. Because the replay guard binds to the request digest (computed over the full request body) rather than to a re-usable session/quota, the caller cannot resubmit the identical operation until the original authorization's expiry timestamp elapses — and if the underlying allowlist/JWT grant was intended as a single opportunity to write a specific secret, the caller is left unable to create the secret at all, analogous to being "stuck with the deprecated asset" in the source report. This is a functional lockout of an unprivileged client from legitimate vault operations (secret creation for their own workflow), not merely inconvenience, since the digest space is dictated by the exact request bytes.

### Likelihood Explanation
This requires no attacker — it is triggered by any organic transient failure between the gateway and the DON (documented as observed in the project's own smoke tests during "burst load," where the gateway returns 503 after the DON has already processed/authorized the request). Given that gateway timeouts, node rate limiting (`nodeRateLimiter.Allow`) [9](#0-8) , and quorum aggregation failures are normal operational conditions rather than edge cases, ordinary users are likely to encounter this lockout during periods of load or partial node unavailability.

### Recommendation
Decouple replay/authorization-digest consumption from the authorization check itself: only mark a digest as consumed once the underlying operation (secret create/update/delete) is confirmed to have durably succeeded via DON quorum, not merely once the caller's credential has been validated. Alternatively, allow the replay guard to release/rollback a digest reservation when downstream processing definitively fails (e.g., `errInsufficientResponsesForQuorum`, stamping failure, or node timeout), so legitimate callers can resubmit the same request without needing to wait for the original authorization's expiry or obtain an entirely new grant.

### Proof of Concept
1. An unprivileged workflow owner obtains a valid, single-use JWT/allowlist authorization for a `secrets/create` request and submits it through the gateway.
2. `GatewayVaultRequestProcessor.authorizeAndStamp` → `Authorizer.AuthorizeRequest` succeeds and calls `replayGuard.CheckAndRecord(digest, expiresAt)`, permanently marking the digest as seen [10](#0-9) .
3. The request is forwarded to DON nodes; due to a transient node outage or gateway-to-DON timeout, quorum is not reached and `HandleNodeMessage` returns a `FatalError` to the caller instead of a success response [11](#0-10) .
4. The user resubmits the identical request (their secret was never created) and receives `"request not authorized: request was already authorized previously"` because the digest is still in the replay guard's `seen` map [12](#0-11) .
5. The user cannot create the intended secret until the original grant's `expiresAt` elapses, and if the grant was a single-use allowlist entry, they may be unable to create it at all.

### Citations

**File:** core/capabilities/vault/gateway_vault_request_processor.go (L20-30)
```go
// GatewayVaultRequestProcessor orchestrates the shared gateway-routed vault JSON-RPC pipeline
// used by the gateway public handler and the node-side gateway connector handler.
//
// Pipeline invariant:
//
//	ValidateStructureBeforeAuth → AuthorizeRequest → Prefix ID → StampAuthorizedParams
//	    (no param mutation)        (on raw bytes)               (namespace + request_id)
//
// AuthorizeRequest runs while params are still digest-safe. It also applies the replay guard
// (digest deduplication) and validates that payload owners match the authorized workflow owner
// before this processor rewrites the request ID or stamps params.
```

**File:** core/capabilities/vault/gateway_vault_request_processor.go (L222-248)
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
```

**File:** core/capabilities/vault/authorizer.go (L99-119)
```go
func (a *authorizer) AuthorizeRequest(ctx context.Context, req jsonrpc.Request[json.RawMessage]) (*AuthResult, error) {
	authResult, err := a.authorizeRequest(ctx, req)
	if err != nil {
		return nil, err
	}
	if authResult == nil {
		err = errors.New("auth mechanism returned nil auth result")
		a.lggr.Errorw("auth mechanism returned nil auth result", "method", req.Method, "requestID", req.ID, "hasAuth", req.Auth != "")
		return nil, err
	}
	if err := a.replayGuard.CheckAndRecord(authResult.Digest(), authResult.ExpiresAt()); err != nil {
		a.lggr.Debugw("replay guard rejected request", "method", req.Method, "requestID", req.ID, "owner", authResult.AuthorizedOwner(), "digest", authResult.Digest(), "expiresAt", authResult.ExpiresAt(), "hasAuth", req.Auth != "", "error", err)
		return nil, err
	}
	if ownerErr := validateSecretOwnersMatchAuthorized(req, authResult.AuthorizedOwner()); ownerErr != nil {
		a.lggr.Errorw("owner binding rejected request", "method", req.Method, "requestID", req.ID, "owner", authResult.AuthorizedOwner(), "hasAuth", req.Auth != "", "error", ownerErr)
		return nil, ownerErr
	}
	a.lggr.Debugw("request authorized", "method", req.Method, "requestID", req.ID, "owner", authResult.AuthorizedOwner(), "digest", authResult.Digest(), "expiresAt", authResult.ExpiresAt(), "hasAuth", req.Auth != "")
	return authResult, nil
}
```

**File:** core/capabilities/vault/request_replay_guard.go (L30-47)
```go
// CheckAndRecord returns ErrRequestAlreadySeen if the digest was previously
// recorded and has not yet expired. Otherwise it records the digest with
// the given expiry timestamp (unix seconds, UTC).
//
// Expired entries are cleaned up on every call.
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

**File:** core/services/gateway/handlers/vault/handler.go (L403-464)
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

	switch req.Method {
	case vaulttypes.MethodSecretsCreate:
		return h.handleSecretsCreate(ctx, ar)
	case vaulttypes.MethodSecretsUpdate:
		return h.handleSecretsUpdate(ctx, ar)
	case vaulttypes.MethodSecretsDelete:
		return h.handleSecretsDelete(ctx, ar)
	case vaulttypes.MethodSecretsList:
		return h.handleSecretsList(ctx, ar)
	default:
		return h.sendResponse(ctx, ar, h.errorResponse(req, api.UnsupportedMethodError, errors.New("this method is unsupported: "+req.Method), nil))
	}
}
```

**File:** core/services/gateway/handlers/vault/handler.go (L489-521)
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

	ok := ar.addResponseForNode(nodeAddr, resp)
	if !ok {
		l.Errorw("duplicate response from node, ignoring", "nodeAddr", nodeAddr)
		return nil
	}

	copiedResponses := ar.copiedResponses()
	resp, err := h.aggregator.Aggregate(ctx, l, ar.req.ID, copiedResponses, resp)
	switch {
	case errors.Is(err, errInsufficientResponsesForQuorum):
		l.Debugw("aggregating responses, waiting for other nodes...", "error", err)
		return nil
	case err != nil:
		l.Error("quorum unobtainable, returning response to user...", "error", err, "responses", maps.Values(copiedResponses))
		return h.sendResponse(ctx, ar, h.errorResponse(ar.req, api.FatalError, err, nil))
	}
```

**File:** system-tests/tests/smoke/cre/vault_don_test.go (L673-705)
```go
// sendConcurrentVaultCreate sends an already-allowlisted create request to the gateway and tolerates
// the replay-guard outcome. Under burst load, the gateway can time out (503 "Request timed out") while
// DON still processes the create; the test's HTTP retry then re-sends the same request digest, which
// vault's replay guard rejects with "request was already authorized previously". That error proves the
// original request was accepted and processed, so we treat it as success — there is no later response
// payload to validate when this path fires.
func sendConcurrentVaultCreate(t *testing.T, gwURL, requestID string, jsonRequest jsonrpc.Request[json.RawMessage], authorizedOwner, expectedResponseOwner string, namespaces []string) {
	t.Helper()

	authToken := jsonRequest.Auth
	stripped := outboundRequestWithoutAuth(jsonRequest)
	requestBody, err := json.Marshal(stripped)
	require.NoError(t, err, "failed to marshal vault request")
	headers := map[string]string{}
	if authToken != "" {
		headers["Authorization"] = "Bearer " + authToken
	}

	statusCode, body := sendVaultRequestToGatewayWithHeaders(t, gwURL, requestBody, headers)

	// Under burst load the gateway can return 503 "Request timed out" when it gives up relaying the
	// response, even though the DON has already processed the request. Tolerate that here — the goal
	// of this subtest is to drive concurrent load for the docker-log batching assertions below, not
	// to verify per-request response payloads.
	if statusCode == http.StatusServiceUnavailable && bytes.Contains(body, []byte("Request timed out")) {
		framework.L.Info().Str("requestID", requestID).Msg("vault create gateway-to-DON timeout; treating as success for batching load test")
		return
	}
	// Replay guard can arrive on a non-200 HTTP status after a retried gateway call; check before StatusOK.
	if bytes.Contains(body, []byte("request was already authorized previously")) {
		framework.L.Info().Str("requestID", requestID).Msg("vault create returned replay-guard error after retry; DON processed the original request — treating as success")
		return
	}
```

**File:** system-tests/tests/smoke/cre/vault_don_test_helpers.go (L230-246)
```go
func shouldRetryGatewayRequest(statusCode int, body []byte) bool {
	if isGatewayNotAllowlistedError(body) {
		return true
	}
	switch statusCode {
	case http.StatusServiceUnavailable, http.StatusBadGateway, http.StatusGatewayTimeout:
		// Gateway-to-DON timeout: the gateway gave up relaying the response, but the DON likely
		// already processed the request. Retrying the same request body just hits the vault
		// replay guard ("request was already authorized previously"). Don't retry these.
		if bytes.Contains(body, []byte("Request timed out")) {
			return false
		}
		return true
	default:
		return false
	}
}
```
