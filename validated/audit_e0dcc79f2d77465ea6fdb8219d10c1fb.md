### Title
Confidential relay handler fans out `MethodSecretsGet` (and `MethodCapabilityExec`) requests to all DON nodes with no authorization/auth check, unlike vault handler - ([File: core/services/gateway/handlers/confidentialrelay/handler.go])

### Summary
`handler.HandleJSONRPCUserMessage` in the confidential relay handler validates only `req.ID` (non-empty, ≤200 chars) and then unconditionally calls `newActiveRequest` followed by `fanOutToNodes`, with no authorization/authentication gate on `req.Auth` or the requested method. This is unlike the vault handler's `HandleJSONRPCUserMessage`, which calls `h.requestProcessor.ProcessRequest(ctx, &req, cachedPublicKey)` before dispatching secrets methods.

### Finding Description
`gateway.ProcessRequest` decodes the raw JSON-RPC request (with whatever `auth` string was supplied, including empty) and routes it straight to `h.HandleJSONRPCUserMessage(ctx, jsonRequest, callback)` [1](#0-0) . For the confidential relay handler, `HandleJSONRPCUserMessage` only checks `req.ID` length constraints, then immediately builds an `activeRequest` and fans the request out to every DON member via `h.fanOutToNodes` [2](#0-1) . There is no call comparable to the vault handler's authorization gate, no check of `req.Auth`, no allowlist/JWT verification, and no per-route (view/run/edit/admin) authorization distinction for `MethodSecretsGet` vs `MethodCapabilityExec` — both are accepted identically in `Methods()` [3](#0-2) .

By contrast, the vault handler explicitly gates all non-public-key methods through `h.requestProcessor.ProcessRequest(ctx, &req, cachedPublicKey)`, rejecting with "request not authorized" on failure, and only then dispatches to `handleSecretsCreate/Update/Delete/List` [4](#0-3) . The confidential relay handler has no analogous `requestProcessor`/`authorizer` field at all — its `handler` struct only holds rate limiters, DON config, and the bundler, with no authorization component [5](#0-4) .

The tests in `handler_test.go` confirm this behavior: `TestConfidentialRelayHandler_ForwardsBundleAtQuorum` and others invoke `HandleJSONRPCUserMessage` with a bare `jsonrpc.Request` (no `Auth` field populated) and it is unconditionally forwarded to nodes and produces a successful response once quorum responses are collected [6](#0-5) . No test exists asserting rejection of an unauthenticated/unauthorized `MethodSecretsGet` request — only request-ID validation is tested (`TestConfidentialRelayHandler_EmptyRequestID`, `TestConfidentialRelayHandler_RequestIDTooLong`) [7](#0-6) .

### Impact Explanation
An unauthenticated network caller can submit a JSON-RPC request with `method: relaytypes.MethodSecretsGet` and empty/absent `auth`, and the gateway will fan it out to every configured relay-DON node via `SendToNode`, with the DON nodes then processing/returning secrets-bundle data for aggregation and forwarding back to the (unauthenticated) caller. This maps to a secrets/credential disclosure bounty class — an attacker with only network access to the gateway user HTTP endpoint can trigger retrieval of confidential-relay-managed secrets without any session, API token, or EI credential, contrary to the exact-per-route authorization invariant enforced elsewhere (vault handler).

### Likelihood Explanation
Preconditions are minimal: only network reachability to the gateway's user-facing HTTP endpoint is required, with no session/API token/EI credential and no special role. The request is a straightforward, repeatable JSON-RPC POST (`{"method": "confidentialrelay_secretsGet", "id": "...", ...}`), and nothing in `HandleJSONRPCUserMessage`, `newActiveRequest`, or `fanOutToNodes` inspects `req.Auth` or performs any signature/allowlist check before dispatch, making exploitation deterministic and trivially repeatable.

### Recommendation
Add an authorization gate to `confidentialrelay.handler.HandleJSONRPCUserMessage` analogous to the vault handler's `requestProcessor.ProcessRequest`: validate `req.Auth` (e.g., signature/allowlist/JWT) and enforce per-method authorization (distinguishing `MethodSecretsGet` from `MethodCapabilityExec`) before calling `newActiveRequest`/`fanOutToNodes`. Reject unauthorized requests immediately with an appropriate error response instead of forwarding to the DON.

### Proof of Concept
Add a handler-level Go test in `core/services/gateway/handlers/confidentialrelay/handler_test.go`:
1. Build a `handler` via `setupHandler(t, N)` as existing tests do.
2. Construct `req := jsonrpc.Request[json.RawMessage]{ID: "req-unauth", Method: MethodSecretsGet, Auth: ""}` (no `Auth`, no valid params/signature).
3. Call `err := h.HandleJSONRPCUserMessage(t.Context(), req, cb)`.
4. Assert current behavior: `require.NoError(t, err)` and that the mocked DON's `SendToNode` was invoked for all DON members (e.g., `don.AssertCalled(t, "SendToNode", ...)` or count of calls == number of DON members) — demonstrating the request is fanned out despite carrying no authorization, in contrast to the vault handler's `TestVaultHandler_*Unauthorized` style tests that assert an error/rejection before any `SendToNode` call.

### Citations

**File:** core/services/gateway/gateway.go (L264-273)
```go
	startTime := time.Now()
	var method string
	callback := handlerscommon.NewCallback()
	if isLegacyRequest {
		method = msg.Body.Method
		err = h.HandleLegacyUserMessage(ctx, msg, callback)
	} else {
		method = jsonRequest.Method
		err = h.HandleJSONRPCUserMessage(ctx, jsonRequest, callback)
	}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L174-195)
```go
type handler struct {
	services.StateMachine
	donConfig *config.DONConfig
	don       gwhandlers.DON
	codec     api.JsonRPCCodec
	lggr      logger.Logger
	mu        sync.RWMutex
	stopCh    services.StopChan

	globalNodeRateLimiter limits.RateLimiter
	perNodeRateLimiters   map[string]limits.RateLimiter
	requestTimeout        time.Duration
	nodeSendTimeout       time.Duration
	quorumGrace           time.Duration

	activeRequests map[string]*activeRequest
	metrics        *metrics

	bundler relayBundler

	clock clockwork.Clock
}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L341-343)
```go
func (h *handler) Methods() []string {
	return []string{MethodSecretsGet, MethodCapabilityExec}
}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L349-366)
```go
func (h *handler) HandleJSONRPCUserMessage(ctx context.Context, req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) error {
	if req.ID == "" {
		return errors.New("request ID cannot be empty")
	}
	if len(req.ID) > 200 {
		return errors.New("request ID is too long: " + strconv.Itoa(len(req.ID)) + ". max is 200 characters")
	}

	l := logger.With(h.lggr, "method", req.Method, "requestID", req.ID)
	l.Debugw("handling confidential relay request")

	ar, err := h.newActiveRequest(req, callback)
	if err != nil {
		return err
	}

	return h.fanOutToNodes(ctx, l, ar)
}
```

**File:** core/services/gateway/handlers/vault/handler.go (L431-463)
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
```

**File:** core/services/gateway/handlers/confidentialrelay/handler_test.go (L170-196)
```go
func TestConfidentialRelayHandler_RequestIDTooLong(t *testing.T) {
	t.Parallel()
	h, cb, _, _ := setupHandler(t, 4)

	longID := strings.Repeat("x", 201)
	req := jsonrpc.Request[json.RawMessage]{
		ID:     longID,
		Method: MethodCapabilityExec,
	}

	err := h.HandleJSONRPCUserMessage(t.Context(), req, cb)
	expected := fmt.Sprintf("request ID is too long: %d. max is 200 characters", len(longID))
	require.EqualError(t, err, expected)
}

func TestConfidentialRelayHandler_EmptyRequestID(t *testing.T) {
	t.Parallel()
	h, cb, _, _ := setupHandler(t, 4)

	req := jsonrpc.Request[json.RawMessage]{
		ID:     "",
		Method: MethodCapabilityExec,
	}

	err := h.HandleJSONRPCUserMessage(t.Context(), req, cb)
	require.EqualError(t, err, "request ID cannot be empty")
}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler_test.go (L202-229)
```go
func TestConfidentialRelayHandler_ForwardsBundleAtQuorum(t *testing.T) {
	t.Parallel()
	h, cb, don, _ := setupHandler(t, 4)
	don.On("SendToNode", mock.Anything, mock.Anything, mock.Anything).Return(nil)

	params := validCapParamsJSON("wf1")
	req := jsonrpc.Request[json.RawMessage]{
		ID:     "req-quorum",
		Method: MethodCapabilityExec,
		Params: &params,
	}
	result := relaytypes.CapabilityResponseResult{Payload: "result"}

	var wg sync.WaitGroup
	wg.Go(func() {
		resp, err := cb.Wait(t.Context())
		assert.NoError(t, err)
		assert.Equal(t, api.NoError, resp.ErrorCode)
		var jsonResp jsonrpc.Response[json.RawMessage]
		assert.NoError(t, json.Unmarshal(resp.RawResponse, &jsonResp))
		assert.NotNil(t, jsonResp.Result)
		var bundle relaytypes.SignedCapabilityResponseBundle
		assert.NoError(t, json.Unmarshal(*jsonResp.Result, &bundle))
		assert.Len(t, bundle.Responses, 3, "the gateway forwards every collected signed response")
	})

	err := h.HandleJSONRPCUserMessage(t.Context(), req, cb)
	require.NoError(t, err)
```
