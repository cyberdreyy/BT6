## Analysis

The reported bug class is a **signed/negative-value mishandling that causes a security check which is supposed to reject bad state to instead always pass**. The closest concrete analog reachable by an unprivileged, unauthenticated actor in this codebase is the "stale message" freshness check in the internet-facing Gateway's legacy user-message handler.

### Title
Integer sign/overflow bug lets an unprivileged Gateway client bypass the anti-replay "stale message" check via a negative `Timestamp` - (File: `core/services/gateway/handlers/capabilities/handler.go`)

### Summary
`handler.HandleLegacyUserMessage`, reached directly from the public, unauthenticated `gateway.ProcessRequest` HTTP endpoint, validates message freshness using unsigned integer arithmetic on an attacker-supplied, signed `int64` `Timestamp` field. Because the code only special-cases `Timestamp == 0` and then converts a fully attacker-controlled `int64` to `uint` for the freshness comparison, a negative timestamp value wraps around to a very large unsigned number, causing the intended "reject if too old" comparison to never trigger.

### Finding Description
`gateway.ProcessRequest` is the externally reachable entry point for user HTTP requests [1](#0-0) . For legacy requests it calls `h.HandleLegacyUserMessage(ctx, msg, callback)` after only validating the JSON-RPC envelope/signature shape, not the payload semantics [2](#0-1) .

Inside `HandleLegacyUserMessage`, the payload is unmarshalled from attacker-controlled JSON into `webapicap.TriggerRequestPayload`, whose `Timestamp` field is an `int64` set directly from the request body [3](#0-2) . The handler checks:

```go
if payload.Timestamp == 0 { ... }
if uint(time.Now().Unix())-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp) {
    // "stale message"
}
``` [4](#0-3) 

The zero-check does nothing to prevent negative values. Converting a negative `int64` to `uint` produces a value near `math.MaxUint64` (e.g. `uint(-1) == 18446744073709551615`), which makes the right-hand side of the comparison enormous, so `uint(now)-MaxAllowedMessageAgeSec > uint(payload.Timestamp)` is false — the staleness rejection is silently skipped for any negative `Timestamp`. This is structurally identical to the tracer bug pattern: an unchecked negative input flows into a comparison meant to gate on magnitude, and the sign mishandling makes the gating check pass when it should fail.

Notably, the very next line contains a `// TODO: apply allowlist and rate-limiting here` [5](#0-4) , confirming that, at this stage of the pipeline, the freshness/staleness check is one of the only defensive checks present before the message is forwarded to every member of the target DON [6](#0-5) .

### Impact Explanation
An unprivileged, unauthenticated HTTP client to the Gateway can submit a `web_api_trigger` message with a negative `Timestamp`, bypassing the freshness/anti-replay control intended to bound the validity window of trigger requests before they are broadcast to all DON node members. Combined with the adjacent TODO indicating allowlist/rate-limiting is not yet enforced at this layer, this weakens one of the few safeguards currently gating unauthenticated trigger traffic reaching workflow DON nodes.

### Likelihood Explanation
High likelihood of reachability: the vulnerable code executes on every legacy user request to the Gateway's public endpoint without prior authentication, and the `Timestamp` field is fully attacker-controlled JSON input requiring no special permissions to set to a negative number.

### Recommendation
Reject non-positive `Timestamp` values explicitly (e.g., `payload.Timestamp <= 0`) before performing the freshness comparison, and perform the staleness comparison using signed arithmetic (`int64`) rather than converting to `uint`, to avoid sign-related wraparound entirely.

### Proof of Concept
1. Craft a `TriggerRequestPayload` JSON body with `"timestamp": -1` (or any negative integer) and a valid signature over that body from any self-generated key (no privileged credential required).
2. Submit it as a legacy request (with `DonId` set) to the Gateway's public HTTP endpoint (`gateway.ProcessRequest` → `HandleLegacyUserMessage`).
3. Observe that the `payload.Timestamp == 0` check passes (value is `-1`, not `0`), and `uint(time.Now().Unix()) - MaxAllowedMessageAgeSec > uint(-1)` evaluates to `false` since `uint(-1)` wraps to `math.MaxUint64`, so the "stale message" rejection is skipped and the message proceeds to be forwarded to all DON members [7](#0-6) .

### Citations

**File:** core/services/gateway/gateway.go (L217-273)
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
	} else {
		method = jsonRequest.Method
		err = h.HandleJSONRPCUserMessage(ctx, jsonRequest, callback)
	}
```

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L204-215)
```go
		ts, err := strconv.ParseInt(timestamp, 10, 64)
		require.NoError(t, err)
		reqPayload := webapicap.TriggerRequestPayload{
			TriggerId:      "web-api-trigger@1.0.0",
			TriggerEventId: "action_1234567890",
			Timestamp:      ts,
			Topics:         topics,
			Params: webapicap.TriggerRequestPayloadParams(map[string]any{
				"bid": "101",
				"ask": "102",
			}),
		}
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
