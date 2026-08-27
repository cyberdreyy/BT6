### Title
Missing allowlist enforcement in `HandleLegacyUserMessage` allows any signature-valid sender to consume DON node resources and collide savedCallbacks by MessageId - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` dispatches `MethodWebAPITrigger` requests to every DON member node after only checking payload decoding, timestamp freshness, and method name — the code explicitly marks allowlist/rate-limiting as unimplemented (`// TODO: apply allowlist and rate-limiting here`, `core/services/gateway/handlers/capabilities/handler.go:384`). Any attacker who can produce a validly-signed `api.Message` (using a self-generated throwaway ECDSA key) can therefore force the gateway to forward a request to all DON nodes and register an entry in `h.savedCallbacks` keyed only by `msg.Body.MessageId` [1](#0-0) .

### Finding Description
`HandleLegacyUserMessage` validates the payload can be decoded, that `Timestamp != 0`, and that the message isn't stale, then reaches the TODO comment and only checks `msg.Body.Method == MethodWebAPITrigger` before building a JSON-RPC request and broadcasting it to every DON member via `don.SendToNode` [2](#0-1) . There is no subscriber/topic-based check at this layer — the only cryptographic validation is that the message has a syntactically valid signature (performed upstream in `msg.Validate()`/gateway request validation), which merely proves the sender controls *some* private key, not that it is an authorized DON subscriber. Because `h.savedCallbacks` is keyed solely by `msg.Body.MessageId` (attacker-controlled/user-supplied, not derived from sender), a second call using the same `MessageId` value overwrites the previously stored callback for that ID [3](#0-2) .

Downstream, the actual per-workflow authorization is enforced in `triggerConnectorHandler.processTrigger`, which checks `trigger.allowedSenders[sender.String()]` before admitting the event to a registered workflow, rejecting unauthorized senders with `"unauthorized Sender ..."` [4](#0-3) . This means an unauthorized sender's trigger message is still forwarded to and processed by every DON node (each node independently unmarshals, resolves matching topics, and evaluates the allowlist) before being rejected — the gateway performs no filtering, so the cost of rejecting unauthorized senders is pushed entirely onto the DON nodes, not blocked at the gateway edge as the TODO comment (and design intent) implies.

### Impact Explanation
This matches a DON resource-exhaustion / missing-rate-limiting class of impact rather than a full authentication bypass: the actual workflow trigger execution is still gated by `allowedSenders` in `trigger.go`, so an attacker cannot forge accepted webhook triggers into workflows they aren't authorized for. However, the missing gateway-side allowlist means:
- Any unauthenticated (relative to DON subscription) party can force `SendToNode` fan-out to every DON member for a given DON, consuming node bandwidth/CPU and per-node rate-limiter budget (`h.nodeRateLimiter`, `handleWebAPIOutgoingMessage`) that is otherwise meant for legitimate subscribers.
- `h.savedCallbacks` insertion/collision by `MessageId` is a minor bookkeeping concern: since `MessageId` values are attacker-chosen only for the attacker's own request, a naturally occurring collision with another user's concurrent request would require a MessageId guess/collision, which is a much weaker practical vector than described (it doesn't let an attacker read another user's response without also guessing/knowing their exact MessageId).

### Likelihood Explanation
Feasibility is high for the DON-capacity-consumption sub-claim: no credentials beyond the ability to submit a signed gateway message and knowledge of a valid `DonId` are required, and message signing only requires a self-generated key. Feasibility is low for the "bypass allowlist to inject an accepted trigger" framing, since `trigger.go`'s `allowedSenders` check still blocks unauthorized senders from actually triggering workflow executions.

### Recommendation
Implement the allowlist/rate-limiting check flagged by the TODO in `HandleLegacyUserMessage` before forwarding messages to DON nodes — reject unrecognized/unauthorized senders (and enforce per-sender rate limits) at the gateway layer, matching the intent of the comment at `core/services/gateway/handlers/capabilities/handler.go:384`. Additionally, consider keying `h.savedCallbacks` by a composite of sender+MessageId, or validating uniqueness, to avoid ambiguity/collision from attacker-chosen MessageIds.

### Proof of Concept
1. In `core/services/gateway/handlers/capabilities/handler_test.go`, construct two `api.Message` values with identical `Body.MessageId`, `Method = MethodWebAPITrigger`, but signed by two distinct freshly-generated ECDSA keys (one simulating a legitimate registered sender, one an arbitrary attacker key not present in any workflow's `allowedSenders`).
2. Call `handler.HandleLegacyUserMessage` for both messages against a mocked `handlers.DON` (assert `SendToNode` is invoked for both, showing the attacker message was forwarded to all DON members without any allowlist rejection at the gateway).
3. Assert `h.savedCallbacks[msg.Body.MessageId]` after the second call holds the second (attacker's) `Callback`, demonstrating the collision.
4. Separately, add/extend a test in `core/capabilities/webapi/trigger/trigger_test.go` calling `processTrigger` with a sender not in `allowedSenders`, asserting it returns `"unauthorized Sender ..."` and does not emit `TriggerResponse` — showing final trigger admission is still gated downstream, bounding the actual impact to gateway/DON resource consumption rather than authorization bypass into workflow execution.

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-420)
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

**File:** core/capabilities/webapi/trigger/trigger.go (L97-109)
```go
	for _, trigger := range h.registeredWorkflows {
		for _, topic := range topics {
			if trigger.allowedTopics[topic] {
				matchedWorkflows++
				if !trigger.allowedSenders[sender.String()] {
					err = fmt.Errorf("unauthorized Sender %s, messageID %s", sender.String(), body.MessageId)
					h.lggr.Debugw(err.Error())
					continue
				}
				if !trigger.rateLimiter.Allow(body.Sender) {
					err = fmt.Errorf("request rate-limited for sender %s, messageID %s", sender.String(), body.MessageId)
					continue
				}
```
