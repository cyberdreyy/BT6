### Title
Missing replay/nonce protection in `HandleLegacyUserMessage` allows duplicate DON executions from resubmitted signed messages - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` validates `payload.Timestamp` only for staleness (`time.Now().Unix() - MaxAllowedMessageAgeSec > payload.Timestamp`) but never checks that a given signed message (or its `MessageId`) has not already been processed. As a result, resubmitting the exact same signed bytes within the freshness window causes `don.SendToNode` to fire again for every DON member, re-triggering execution for a message that was already fully processed.

### Finding Description
In `core/services/gateway/handlers/capabilities/handler.go`, `HandleLegacyUserMessage` (lines 341-421) performs the following checks on an inbound legacy gateway message: payload decode success, `payload.Timestamp != 0`, and staleness bound via `MaxAllowedMessageAgeSec` [1](#0-0) . There is no check for `MessageId` uniqueness or any digest/nonce replay cache before the message is dispatched: it simply stores a callback keyed by `msg.Body.MessageId` (unconditionally overwriting any prior entry) and then loops `don.SendToNode` for every DON member [2](#0-1) .

The request enters this function via `gateway.ProcessRequest`, which for legacy (DON-scoped) messages calls `msg.Validate()` (signature integrity only) and then `h.HandleLegacyUserMessage(ctx, msg, callback)` with no dedup layer above it either [3](#0-2) . `msg.Validate()`/signature verification only proves the bytes are unmodified and correctly signed — it does nothing to prevent the identical, validly-signed bytes from being submitted more than once.

This is in clear contrast to the newer v2 trigger handler, which explicitly implements in-flight/duplicate request-ID rejection and JWT `jti` replay-caching (`workflowMetadataHandler.jwtCache`, "in-flight request" checks in `http_trigger_handler.go`) [4](#0-3) , and the Vault subsystem's dedicated `RequestReplayGuard` which tracks request digests to reject reprocessing [5](#0-4) . The legacy capabilities handler has no equivalent protection, so `don.SendToNode` will be invoked a second (or Nth) time for the same `MessageId`/payload as long as it is resubmitted before `MaxAllowedMessageAgeSec` elapses, causing the DON to execute the trigger/target/compute action again.

### Impact Explanation
Each successful pass through `HandleLegacyUserMessage` causes the gateway to broadcast the request to every DON member via `don.SendToNode`, driving a new workflow/capability execution on the DON for that request [6](#0-5) . Because the request itself carries no execution-uniqueness guarantee at the gateway layer, resubmission of identical bytes produces duplicate DON-side runs for what should be a single logical request — violating the "one request, one execution" invariant and mapping to Chainlink's "unauthorized/duplicate job run" impact class, with resource exhaustion / duplicate billing against the resource owner whose credentials signed the original message.

### Likelihood Explanation
The only precondition is possession of one validly-signed legacy message with a timestamp still inside `MaxAllowedMessageAgeSec` (default 120s per `defaultCallbackMaxAgeSec`, though `MaxAllowedMessageAgeSec` is separately configured) [7](#0-6) . No signing key or elevated privilege is needed beyond what the original caller used — any party able to resend the exact captured bytes over the gateway's `ProcessRequest` HTTP path can trigger the duplication; it is fully repeatable for the duration of the freshness window, and does not require node, operator, or database access.

### Recommendation
Add a replay-protection mechanism to `HandleLegacyUserMessage` analogous to the v2 handler's dedup logic and Vault's `RequestReplayGuard`: track already-seen `MessageId`s (or full message digests) with TTL equal to `MaxAllowedMessageAgeSec`, and reject/short-circuit with an error (e.g., `jsonrpc.ErrConflict`/`api.HandlerError`) if the ID/digest was already processed, before saving the callback and dispatching to `don.SendToNode`.

### Proof of Concept
1. In `core/services/gateway/handlers/capabilities/handler_test.go`, construct a signed `triggerRequest` message (as in `TestHandlerReceiveHTTPMessageFromClient`) with a fresh timestamp.
2. Call `handler.HandleLegacyUserMessage(ctx, msg, cb1)` and assert `don.SendToNode` is invoked once per DON member for `msg.Body.MessageId`.
3. Immediately call `handler.HandleLegacyUserMessage(ctx, msg, cb2)` again with the exact same `msg` bytes (same `MessageId`, same signature, timestamp still within `MaxAllowedMessageAgeSec`).
4. Assert `don.SendToNode` is invoked a second time with the identical `MessageId`/payload, and that no error/rejection (e.g., `ErrConflict`, "already processed") is returned — demonstrating the absence of replay protection.

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L43-45)
```go
	defaultCallbackMaxAgeSec        = 120   // 2 minutes
	defaultMaxSavedCallbacks        = 20000 // could briefly exceed under heavy load
	defaultCallbackPruneIntervalSec = 30
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L359-383)
```go
	if payload.Timestamp == 0 {
		h.lggr.Errorw(ErrDecodingPayload)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UserMessageParseError),
				ErrDecodingPayload,
				nil,
			),
			ErrorCode: api.UserMessageParseError,
		})
	}

	if uint(time.Now().Unix())-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp) {
		h.lggr.Errorw("stale message")
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.HandlerError),
				"stale message",
				nil,
			),
			ErrorCode: api.HandlerError,
		})
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-420)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()

	// Send original request to all nodes
	for _, member := range h.donConfig.Members {
		err = errors.Join(err, don.SendToNode(ctx, member.Address, req))
	}
	return err
```

**File:** core/services/gateway/gateway.go (L250-273)
```go
	} else {
		// Legacy request with DON ID - validate and fetch handler
		isLegacyRequest = true
		if err = msg.Validate(); err != nil {
			return newError(jsonRequest.ID, api.UserMessageParseError, err.Error())
		}
		handlerKey = msg.Body.DonId
		var ok bool
		h, ok = g.handlers[handlerKey]
		if !ok {
			return newError(jsonRequest.ID, api.UnsupportedDONIdError, "Unsupported DON ID: "+handlerKey)
		}
	}

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

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L80-107)
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
```

**File:** core/capabilities/vault/request_replay_guard.go (L9-47)
```go
var ErrRequestAlreadySeen = errors.New("request was already authorized previously")

// RequestReplayGuard prevents replay of already-processed requests by tracking
// request digests with expiry timestamps. It is safe for concurrent use.
//
// Used by both the AllowListBasedAuth flow and the JWTBasedAuth flow to ensure
// that a given request digest is only accepted once.
type RequestReplayGuard struct {
	mu      sync.Mutex
	seen    map[string]int64 // digest → unix expiry timestamp
	nowFunc func() time.Time // injectable for testing
}

// NewRequestReplayGuard creates a replay guard for authorized Vault requests.
func NewRequestReplayGuard() *RequestReplayGuard {
	return &RequestReplayGuard{
		seen:    make(map[string]int64),
		nowFunc: time.Now,
	}
}

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
