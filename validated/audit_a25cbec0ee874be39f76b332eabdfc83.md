Confirmed: there is no MessageId/nonce dedup check anywhere in the reachable path. `ProcessRequest` in `core/services/gateway/gateway.go` decodes the JSON-RPC request, validates the message via `msg.Validate()` (signature check only), and dispatches straight to `h.HandleLegacyUserMessage`. `HandleLegacyUserMessage` in `core/services/gateway/handlers/capabilities/handler.go` only checks `payload.Timestamp == 0` and staleness against `MaxAllowedMessageAgeSec`, then calls `common.ValidatedRequestFromMessage` and forwards the request to every DON member unconditionally. [1](#0-0) [2](#0-1) 

The only place a `MessageId` is tracked at all is `h.savedCallbacks[msg.Body.MessageId]`, which is used purely to route the DON's response back to the caller and is deleted immediately once a node's answer arrives — it is not a request-dedup/anti-replay structure, and even if the attacker reuses the same `MessageId`, this map does not block re-forwarding the request to `don.SendToNode` for all DON members. [3](#0-2) 

### Title
Missing anti-replay (nonce/message-id) protection allows replay of a captured signed trigger message within staleness window - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` validates a signed `api.Message`'s signature and checks only timestamp staleness (`payload.Timestamp` vs `MaxAllowedMessageAgeSec`) before forwarding the request to every DON node. There is no nonce, message-id uniqueness check, or replay cache, so any previously captured valid signed message can be resubmitted verbatim by an unauthenticated caller as long as it is still inside the staleness window, causing the workflow trigger to fire again.

### Finding Description
An attacker who intercepts one legitimate signed `api.Message` (containing a `webapicap.TriggerRequestPayload`) can resend the identical byte-for-byte HTTP/gateway request multiple times before `payload.Timestamp` exceeds `MaxAllowedMessageAgeSec`. `ProcessRequest` (core/services/gateway/gateway.go:218-269) decodes the JSON-RPC envelope, calls `msg.Validate()` (signature verification only — no freshness or uniqueness tracking), and routes to `HandleLegacyUserMessage`. That function checks `payload.Timestamp == 0` and staleness (lines 359-383), then calls `common.ValidatedRequestFromMessage(msg)` and forwards the exact same request to all DON members (lines 397-419). Because the signature is over the message body/payload, replaying the identical bytes produces an identical valid signature every time, and nothing in the code path tracks previously-seen `MessageId`s, nonces, or payload hashes to reject a duplicate. The `savedCallbacks` map keyed by `MessageId` exists only to route the async DON response back to the HTTP caller and is deleted as soon as a response arrives — it provides no anti-replay guarantee for a second, later submission of the same message.

### Impact Explanation
This maps to unauthorized/duplicate job (workflow) execution: an attacker who can capture a single signed trigger message (e.g., via network observation, logs, or a misconfigured client that leaks it) can retrigger the exact same workflow execution multiple times within the configurable staleness window (default related config value, up to `MaxAllowedMessageAgeSec`), impersonating the original authorized sender without holding their private key. Depending on the workflow, duplicate executions could cause duplicate on-chain actions, duplicate fund movement, or resource exhaustion via repeated compute-triggering.

### Likelihood Explanation
Preconditions: the attacker must intercept/capture one legitimate signed message (this is the stated precondition and is assumed available to the attacker, e.g., via network capture, log exposure, or a compromised/observing intermediary). No signing key or additional credential is needed — the captured signature remains valid. Exploitation is trivial and fully repeatable: replay the same HTTP body any number of times before the timestamp ages out; each replay is processed as new because there is no per-message dedup.

### Recommendation
Add explicit anti-replay protection in `HandleLegacyUserMessage` (or upstream in `gateway.ProcessRequest`): maintain a bounded, TTL-based cache of already-processed `(sender, MessageId)` or `(sender, payload hash, timestamp)` tuples sized/expired to match `MaxAllowedMessageAgeSec`, and reject any request matching an entry already in the cache with a distinct error code (e.g., "duplicate/replayed message"). This should be checked before forwarding to `don.SendToNode`.

### Proof of Concept
Go table/unit test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Build a valid `api.Message` with `MethodWebAPITrigger` and a `webapicap.TriggerRequestPayload{Timestamp: time.Now().Unix()}`, sign it with a test sender key.
2. Call `handler.HandleLegacyUserMessage(ctx, msg, callback1)` — assert it forwards to `don.SendToNode` for all DON members (mock expects call once) and returns success.
3. Immediately call `handler.HandleLegacyUserMessage(ctx, msg, callback2)` with the exact same signed `msg` (same `MessageId`, same signature, same timestamp, well within `MaxAllowedMessageAgeSec`).
4. Assert (expected fix behavior, currently failing): `don.SendToNode` is NOT invoked a second time, and `callback2` receives an error response indicating "duplicate/replayed message" rather than being forwarded to the DON again.
5. Current code will fail this assertion — `don.SendToNode` mock will show two invocations, demonstrating the missing replay protection.

### Citations

**File:** core/services/gateway/gateway.go (L218-269)
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
	}
	var isLegacyRequest = false
	var h handlers.Handler
	var handlerKey string
	if msg == nil || msg.Body.DonId == "" {
		serviceName := jsonRequest.ServiceName()
		if handler, ok := g.serviceToMultiHandler[serviceName]; ok {
			h = handler
			handlerKey = serviceName
		} else if donID, ok := g.serviceNameToDonID[serviceName]; ok {
			// Fallback to legacy service name -> DON ID mapping
			if handler, ok := g.handlers[donID]; ok {
				h = handler
				handlerKey = donID
			}
		}
		if h == nil {
			return newError(jsonRequest.ID, api.HandlerError, "Service name not found: "+serviceName)
		}
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
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L359-420)
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
	req, err := common.ValidatedRequestFromMessage(msg)
	if err != nil {
		h.lggr.Errorw(ErrTransformingMessageToRequest)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UserMessageParseError),
				ErrTransformingMessageToRequest,
				nil,
			),
			ErrorCode: api.UserMessageParseError,
		})
	}

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
