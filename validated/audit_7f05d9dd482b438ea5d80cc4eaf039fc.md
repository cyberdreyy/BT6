### Title
Legacy user-triggered messages lack per-sender de-duplication/rate-limiting, allowing signed replay of same-Timestamp payloads with distinct MessageIds to fan out repeated DON-wide dispatches - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.HandleLegacyUserMessage` validates only payload non-emptiness, `Timestamp != 0`, and staleness (`Timestamp` within `MaxAllowedMessageAgeSec`), then unconditionally saves a callback keyed by `MessageId` and broadcasts the request to every member in `h.donConfig.Members`. There is no per-sender quota, no de-duplication on `(Sender, Timestamp)` or payload content, and the code explicitly marks this gap with `// TODO: apply allowlist and rate-limiting here`.

### Finding Description
The flow is:
1. `HandleLegacyUserMessage` unmarshals `body.Payload` into `webapicap.TriggerRequestPayload` and checks `payload.Timestamp != 0` and freshness against `MaxAllowedMessageAgeSec` [1](#0-0) .
2. Immediately after, the code comments `// TODO: apply allowlist and rate-limiting here` and only checks that `msg.Body.Method == MethodWebAPITrigger` [2](#0-1) .
3. It stores the callback under `h.savedCallbacks[msg.Body.MessageId]` and fans the request out to every DON member via `don.SendToNode` in a loop [3](#0-2) .

Because de-duplication is keyed only by `MessageId` (not by sender+timestamp+payload hash), an attacker holding any valid signing key can construct multiple distinct signed messages, each with a unique `MessageId` but the same `Timestamp` (still within the staleness window), and each will independently pass all checks and trigger a full DON-wide broadcast (`don.SendToNode` for every member). There is no `nodeRateLimiter`-equivalent applied to the *incoming user* path (that rate limiter, `h.nodeRateLimiter`, is only invoked in `handleWebAPIOutgoingMessage` for node-originated responses, not for incoming legacy user triggers) [4](#0-3) . This confirms no rate-limiting/allowlisting is applied on this path, exactly as the inline TODO states.

By contrast, the newer v2 HTTP trigger handler (`httpTriggerHandler.HandleUserTriggerRequest`) does enforce `authorizeRequest` and `checkRateLimit` (per-workflow rate limiter) before fan-out [5](#0-4) , and the confidential-relay and vault handlers apply authorization/`requestProcessor.ProcessRequest` before dispatch [6](#0-5) . The legacy capabilities handler is the outlier lacking this protection.

### Impact Explanation
Each additional signed message with a unique `MessageId` causes a full DON-wide fan-out (one `SendToNode` call per DON member) and an entry in `h.savedCallbacks`, which is bounded only by `defaultMaxSavedCallbacks` (20000) and periodic pruning every `CallbackPruneIntervalSec` [7](#0-6) . An attacker who can generate many distinct signed payloads within the staleness window can drive repeated DON-wide dispatch traffic and memory growth in `savedCallbacks`, consuming gateway and DON node resources. This matches a resource-exhaustion / availability-degradation impact class rather than fund loss or authentication bypass — there is no unauthorized access to another user's job, secret, or funds since the message content/payload is unchanged and still requires a valid signature transformable via `common.ValidatedRequestFromMessage`.

### Likelihood Explanation
Preconditions are low: the attacker only needs the ability to produce validly-signed legacy messages (any signing key, no special role, as the audit scope states). Repeating the request with a new `MessageId` each time is trivial and requires no coordination with DON members. The `MaxAllowedMessageAgeSec` window gives an attacker a bounded but reusable time window in which arbitrarily many distinct-`MessageId` submissions with the same `Timestamp` will all pass staleness checks, making the issue easily repeatable and low-effort to exploit, limited mainly by the sender's own throughput to construct/sign requests.

### Recommendation
Add per-sender rate limiting/allowlisting to `HandleLegacyUserMessage` before the fan-out to `donConfig.Members`, similar to the `userRateLimiter`/`checkRateLimit` pattern used in `httpTriggerHandler.HandleUserTriggerRequest`. Additionally, consider de-duplicating on `(Sender, Timestamp, payload hash)` — not just `MessageId` — within the staleness window to prevent multiple distinct-`MessageId` submissions carrying materially identical intent from independently triggering DON-wide dispatch. Bound `savedCallbacks` growth per sender.

### Proof of Concept
Go handler-level test plan (extend `core/services/gateway/handlers/capabilities/handler_test.go`):
1. Construct a `handler` via `NewHandler` with a `donConfig.Members` list of N mock nodes and a `handlers.DON` mock expecting `SendToNode` calls.
2. Build a valid `*api.Message` with `MethodWebAPITrigger`, payload containing `Timestamp = now`, and a unique `MessageId = "id-1"`, properly signed so `common.ValidatedRequestFromMessage` succeeds.
3. Call `h.HandleLegacyUserMessage(ctx, msg1, callback1)`; assert `don.SendToNode` was called once per member (N times).
4. Build a second message with identical `Timestamp`, identical payload body, but `MessageId = "id-2"` and a fresh valid signature; call `h.HandleLegacyUserMessage(ctx, msg2, callback2)`.
5. Assert `don.SendToNode` is called N more times (total 2N) with no rejection, no rate-limit error, and both callbacks registered in `h.savedCallbacks` — demonstrating unlimited fan-out amplification per sender absent any de-duplication or rate limiting, consistent with the inline `// TODO: apply allowlist and rate-limiting here` gap at [8](#0-7) .

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L43-45)
```go
	defaultCallbackMaxAgeSec        = 120   // 2 minutes
	defaultMaxSavedCallbacks        = 20000 // could briefly exceed under heavy load
	defaultCallbackPruneIntervalSec = 30
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L164-168)
```go
func (h *handler) handleWebAPIOutgoingMessage(ctx context.Context, msg *api.Message, nodeAddr string) error {
	h.lggr.Debugw("handling webAPI outgoing message", "messageId", msg.Body.MessageId, "nodeAddr", nodeAddr)
	if !h.nodeRateLimiter.Allow(nodeAddr) {
		return fmt.Errorf("rate limit exceeded for node %s", nodeAddr)
	}
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-396)
```go
	// TODO: apply allowlist and rate-limiting here
	if msg.Body.Method != MethodWebAPITrigger {
		h.lggr.Errorw("unsupported method", "method", body.Method)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UnsupportedMethodError),
				"invalid method "+msg.Body.Method,
				nil,
			),
			ErrorCode: api.UnsupportedMethodError,
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

**File:** core/services/gateway/handlers/vault/handler.go (L431-443)
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
```
