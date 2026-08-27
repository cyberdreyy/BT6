### Title
Gateway `ProcessRequest` and downstream JSON-RPC handlers lack replay/nonce protection, allowing re-execution of a previously captured signed request - ([File: core/services/gateway/gateway.go])

### Summary
`gateway.ProcessRequest` performs no tracking of previously-seen request IDs or signatures; it only validates ID length and dispatches to a handler. For the JSON-RPC path (e.g. the vault handler and other `serviceToMultiHandler` handlers), there is no dedup/nonce cache at all, and the vault authorization pipeline (`GatewayVaultRequestProcessor.ProcessRequest` → `Authorizer.AuthorizeRequest`) authorizes based on a deterministic digest of `(ID, method, params)`, which is identical across replays of the same signed bytes.

### Finding Description
In `gateway.ProcessRequest` (core/services/gateway/gateway.go:218-292), the only checks performed on the incoming request are decoding (`jsonrpc2.DecodeRequest`), the `ID` length limit, and routing to the appropriate handler (`h.HandleJSONRPCUserMessage` / `h.HandleLegacyUserMessage`). There is no map, cache, or nonce store keyed by request ID or signature at this layer [1](#0-0) , so `gateway.go` itself trusts handlers entirely for replay protection.

For the new-style JSON-RPC service routing (used by capabilities such as vault and workflow triggers), the dispatched handler is `h.HandleJSONRPCUserMessage`. Looking at the vault handler implementation, `HandleJSONRPCUserMessage` validates ID emptiness/length, then calls `h.requestProcessor.ProcessRequest(ctx, &req, cachedPublicKey)`, which in turn calls `authorizer.AuthorizeRequest` [2](#0-1) . This authorization is digest-based (`req.Digest()` over ID/method/params) as shown in the processor tests, where the authorizer mock matches purely on digest equality with no timestamp/nonce component [3](#0-2) . Because signature/JWT validity is tied only to this static digest, resending byte-identical raw request data reproduces the same digest and passes authorization a second time, with no state recorded anywhere to reject the repeat.

The only in-flight dedup mechanism found in the gateway tree is `RequestCache` used for the legacy DON-message routing path, which rejects concurrent duplicate `(sender, messageId)` pairs with `"request already exists"` [4](#0-3) . However, this cache entry is deleted as soon as the original request completes (success, error, or timeout) via `deleteAndSendOnce` [5](#0-4) , so it only prevents concurrent double-submission while a request is still pending — it does not provide durable replay protection once the first execution has finished. Furthermore, this cache is specific to the legacy `api.Message` DON-routing path; the newer JSON-RPC `serviceToMultiHandler` path (vault, workflow triggers) does not use `RequestCache` at all, so even in-flight dedup is absent there.

### Impact Explanation
An attacker who captures one valid signed JSON-RPC request (e.g., a vault secret write, or a workflow/job trigger request) can resend the identical raw bytes to the gateway endpoint after the original has completed, causing the privileged action to execute a second time. This matches the "unauthorized job run" / duplicate privileged-action impact class, since the gateway and the JSON-RPC handler path provide no binding between a signed request and single execution.

### Likelihood Explanation
The only precondition is possession of one previously valid signed raw request (e.g., via network capture as a low-privileged party) and the ability to POST it again to the gateway HTTP endpoint. No credentials beyond what was needed to produce the original request are required, and the replay is trivially repeatable (send raw bytes again). The legacy-path in-flight cache only blocks concurrent replays before the first completes, which is a narrow race window and does not stop a delayed replay after normal completion.

### Recommendation
Introduce a durable, persistent replay-protection mechanism (e.g., store request digest/ID with a TTL exceeding normal retry windows, or bind requests to a monotonic nonce/timestamp validated against a persisted high-water mark) at either `gateway.ProcessRequest` or within the vault/JSON-RPC request processor's authorization step, rejecting any request whose digest has already been fully processed, not just requests currently in-flight.

### Proof of Concept
1. Build a valid signed vault `MethodSecretsCreate` (or similar) JSON-RPC request `req1` and call `gatewayObj.ProcessRequest(ctx, rawBytes, auth)`; assert success (200) and that the underlying write occurs once.
2. Wait for `RequestCache`/in-flight tracking (if any) to clear (i.e., after the callback response has been sent).
3. Resend the exact same `rawBytes` a second time via `gatewayObj.ProcessRequest(ctx, rawBytes, auth)`.
4. Assert (currently failing) that the second call is rejected with a replay/duplicate error rather than being re-authorized and re-executed — i.e., verify the downstream vault write (or job trigger) mock is invoked exactly once, not twice, across both calls.

### Citations

**File:** core/services/gateway/gateway.go (L218-230)
```go
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

**File:** core/services/gateway/handlers/vault/handler.go (L403-443)
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
```

**File:** core/capabilities/vault/gateway_vault_request_processor_test.go (L41-48)
```go
	digestBefore, err := req.Digest()
	require.NoError(t, err)

	authorizer := vaultcapmocks.NewAuthorizer(t)
	authorizer.EXPECT().AuthorizeRequest(t.Context(), mock.MatchedBy(func(got jsonrpc.Request[json.RawMessage]) bool {
		gotDigest, digestErr := got.Digest()
		return digestErr == nil && gotDigest == digestBefore && got.ID == "req-1"
	})).Return(vault.NewAuthResult("org-test", owner, digestBefore, 0), nil)
```

**File:** core/services/gateway/handlers/common/requestcache.go (L57-63)
```go
	key := globalId{request.Body.Sender, request.Body.MessageId}
	c.mu.Lock()
	defer c.mu.Unlock()
	_, ok := c.cache[key]
	if ok {
		return errors.New("request already exists")
	}
```

**File:** core/services/gateway/handlers/common/requestcache.go (L111-121)
```go
func (c *requestCache[T]) deleteAndSendOnce(key globalId, callbackResponse handlers.UserCallbackPayload) error {
	c.mu.Lock()
	entry, deleted := c.cache[key]
	delete(c.cache, key)
	c.mu.Unlock()
	if deleted {
		entry.timeoutTimer.Stop()
		return entry.SendResponse(callbackResponse)
	}

	return nil
```
