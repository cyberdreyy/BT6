### Title
Unsigned integer underflow/overflow in gateway stale-message check bypasses replay protection - (File: `core/services/gateway/handlers/capabilities/handler.go`)

### Summary
The `HandleLegacyUserMessage` function in the WebAPI capabilities gateway handler validates message freshness using unchecked, unsigned-integer arithmetic mixing a signed, client-controlled timestamp with a `uint` config value. This mirrors the reported bug class (unguarded arithmetic causing underflow/overflow that breaks a security-relevant calculation) from the external Balancer report, except here the analog is reachable by an unprivileged client sending a request through the internet-facing gateway, and the consequence is a security-check bypass rather than a revert.

### Finding Description
`HandleLegacyUserMessage` parses a client-supplied `webapicap.TriggerRequestPayload` and performs the staleness check: [1](#0-0) 

The check is:
```go
if uint(time.Now().Unix())-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp) {
```
`payload.Timestamp` is taken directly from the untrusted, attacker-controlled request body (validated only for `== 0`), and cast to `uint`. Because Go's numeric conversion of a negative (or otherwise out-of-range) signed value to `uint` wraps around using two's-complement semantics rather than raising an exception (unlike Solidity's default checked-arithmetic reverting, but functionally the same class of bug: unguarded arithmetic on attacker-influenced values), an attacker can submit a negative or extremely large `Timestamp` value so that `uint(payload.Timestamp)` becomes an enormous number, always exceeding `uint(time.Now().Unix()) - MaxAllowedMessageAgeSec`. This causes the "stale message" branch to never trigger regardless of the message's actual age.

This is directly analogous to the reported bug class: unguarded subtraction/casting on externally influenced numeric inputs that silently underflows/overflows and defeats the intended safety calculation — except instead of causing a revert (as in Solidity), here it silently disables the freshness/anti-replay check.

Once past this check, the handler proceeds to register a callback and forward the request to all DON members: [2](#0-1) 

### Impact Explanation
The staleness check exists specifically to reject old/replayed messages from unprivileged web API trigger requests reaching the gateway. Bypassing it via a crafted `Timestamp` field allows an unprivileged client to replay or resubmit previously captured trigger messages indefinitely, undermining the anti-replay/freshness guarantee the gateway is meant to enforce for `MethodWebAPITrigger` requests. This is a concrete authentication/validation bypass in an internet-facing gateway handler reachable from any unprivileged sender.

### Likelihood Explanation
The path is trivially reachable: any external client sending a JSON-RPC message to the gateway's WebAPI-capabilities handler controls the `Timestamp` field of the payload, and no other validation constrains it to a valid, bounded range before the arithmetic comparison. No special privileges or node compromise are required — only crafting a request with a negative or out-of-range `Timestamp` value.

### Recommendation
Validate `payload.Timestamp` bounds (reject negative or unreasonably large values) before use, and perform the freshness comparison using signed 64-bit arithmetic (`int64`) instead of casting to `uint`, e.g.:
```go
now := time.Now().Unix()
maxAge := int64(h.config.MaxAllowedMessageAgeSec)
if payload.Timestamp <= 0 || now-maxAge > payload.Timestamp {
    // stale/invalid
}
```
This avoids relying on unsigned wraparound behavior and closes the freshness-check bypass.

### Proof of Concept
1. Attacker sends a `MethodWebAPITrigger` request to the gateway's legacy user endpoint with a `TriggerRequestPayload` whose `Timestamp` field is set to a negative value (e.g., `-1`) or a value exceeding the range that keeps `uint(payload.Timestamp)` large after conversion.
2. In `HandleLegacyUserMessage`, `uint(payload.Timestamp)` wraps to a very large unsigned value.
3. The condition `uint(time.Now().Unix())-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp)` evaluates to `false` regardless of the message's true age, so the "stale message" branch is skipped.
4. The (potentially replayed/old) message is accepted, cached, and forwarded to all DON members via `don.SendToNode`, bypassing the intended freshness/anti-replay control.

Note: I was unable to fully confirm the exact declared Go type (`int64` vs other) of `webapicap.TriggerRequestPayload.Timestamp` from the indexed code (the generated file `trigger_builders_generated.go` was not retrieved in full), so the exact numeric range needed to trigger the wraparound should be verified directly in that generated type definition before remediation.

### Citations

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L410-420)
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
