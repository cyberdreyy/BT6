### Title
Cross-user callback hijack via attacker-controlled MessageId collision in web_api_trigger legacy requests - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` stores the caller's `Callback` in the shared `h.savedCallbacks` map keyed solely by the client-supplied `msg.Body.MessageId`, with no binding to sender identity, and unconditionally overwrites any existing entry for that key. An unauthenticated gateway API client can choose an arbitrary `MessageId`, so racing a victim's in-flight `web_api_trigger` request with an attacker request using the same ID allows the attacker's callback to replace the victim's, and `handleWebAPITriggerMessage` will deliver the DON node's response (destined for whichever request the attacker overwrote) to whichever callback currently occupies that slot.

### Finding Description
The gateway's legacy user-facing `web_api_trigger` flow works as follows:
- A client submits an HTTP/JSON-RPC request; `MessageId` is taken directly from the client-controlled JSON-RPC `id`/legacy message field (`api.JsonRPCCodec.DecodeJSONRequest`, `core/services/gateway/api/jsonrpccodec.go:24-33`, and confirmed attacker-controlled in `invoke_trigger.go` where `messageID` is a free-form CLI/user flag). There is no per-session or per-sender namespacing of this ID.
- `HandleLegacyUserMessage` stores the callback keyed purely by this ID: [1](#0-0) 
This line unconditionally assigns (overwrites) `h.savedCallbacks[msg.Body.MessageId]` with no existence check and no binding to `msg.Body.Sender`.
- When a DON node later responds, `HandleNodeMessage` only validates that `msg.Body.Sender == nodeAddr` (i.e., that the *node* signed the response correctly) — it performs no validation tying the response back to the original requester: [2](#0-1) 
- `handleWebAPITriggerMessage` then looks up and deletes the callback purely by `MessageId` and delivers the payload to whichever callback is currently stored there: [3](#0-2) 

Exploit flow: Victim submits a `web_api_trigger` request with `MessageId = X`; this stores `savedCallbacks[X] = victimCallback` and forwards the request to all DON members (line 417-419). Before the DON responds, an attacker submits their own `web_api_trigger` request also with `MessageId = X`. Because assignment at line 412 has no existence/ownership check, `savedCallbacks[X]` is now overwritten with `attackerCallback`, and a second, distinct DON-bound request (the attacker's) is also fired with the same ID. When the DON node's first response for ID `X` arrives (which could be the answer to either the victim's or attacker's underlying request, since both were sent to the DON with the same `MessageId`), `handleWebAPITriggerMessage` pops whatever callback is currently in the map (now `attackerCallback`, or the victim's if the timing/order is inverted) and sends the response there — resulting in cross-user response delivery. The victim's callback that was silently evicted (map overwrite drops the old pointer without any notification) never receives a response and simply times out or hangs, while the attacker (or victim) receives the wrong data.

Note that whether this qualifies as full "victim's secret data disclosure" depends on payload content, but at minimum it is a callback/response confusion and denial-of-service against the victim's request, and in scenarios where the DON's response payload contains data derived from the original (now-mismatched) trigger event, an unauthorized party ends up receiving a response correlated to the other party's request/DON round-trip.

### Impact Explanation
This matches the "cross-user response confusion" impact class explicitly named in Chainlink's bounty scope: one unauthenticated caller can hijack or clobber another caller's pending gateway callback purely by choosing the same `MessageId`, causing response misdelivery and denial of service for the victim's legacy `web_api_trigger` request. No node/DON/operator compromise is required — the vulnerable code path (`HandleLegacyUserMessage`, `handleWebAPITriggerMessage`) is reachable directly from the unauthenticated gateway user-facing HTTP endpoint.

### Likelihood Explanation
Exploitability requires only: (1) the ability to submit gateway user requests (no privileged credential needed — the legacy `web_api_trigger` endpoint accepts any signed message, and message-signature validation authenticates the sender's key but does not scope `MessageId` uniqueness to that key), and (2) predicting or guessing a victim's in-flight `MessageId` and winning a race before the DON responds (default callback window is up to `defaultCallbackMaxAgeSec` = 120 seconds, `core/services/gateway/handlers/capabilities/handler.go:43`). If `MessageId`s are predictable/sequential/client-chosen text (as shown in `invoke_trigger.go`, where callers pick arbitrary IDs like `"12345"`), this is trivially repeatable. This is a realistic, low-skill, unauthenticated race condition, not a theoretical one.

### Recommendation
Scope `savedCallbacks` (and equivalently `dummyHandler.savedCallbacks` in `core/services/gateway/handlers/handler.dummy.go`) by a composite key of `(sender, MessageId)` rather than `MessageId` alone, mirroring the pattern already used in `core/services/gateway/handlers/common/requestcache.go`'s `globalId{sender, id}`. Additionally, reject `HandleLegacyUserMessage` calls whose `(sender, MessageId)` key already has a pending, non-expired entry instead of silently overwriting it, and ensure the DON-bound request also carries the sender binding so responses can only be matched against the same sender's pending request.

### Proof of Concept
Go table/unit test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Create handler via `setupHandler(t)`.
2. Build `victimMsg` and `attackerMsg`, both `MethodWebAPITrigger` with identical `MessageId = "X"`, signed by two different private keys (`nodes[0].PrivateKey` for victim's originating key vs. a separate `attackerKey`), each with distinct payloads.
3. Call `handler.HandleLegacyUserMessage(ctx, victimMsg, victimCb)` (stores `savedCallbacks["X"] = victimCb`), then immediately call `handler.HandleLegacyUserMessage(ctx, attackerMsg, attackerCb)` before any node response arrives.
4. Assert `handler.savedCallbacks["X"]` now equals `attackerCb`'s wrapped callback (i.e., victim's callback was silently evicted) — demonstrating the overwrite.
5. Simulate a DON node response for `MessageId = "X"` via `handler.HandleNodeMessage(ctx, resp, nodes[0].Address)` and assert that `attackerCb.Wait(ctx)` receives the response instead of `victimCb`, while `victimCb.Wait(ctx)` times out — proving cross-user response misdelivery.

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L148-162)
```go
func (h *handler) handleWebAPITriggerMessage(ctx context.Context, msg *api.Message, nodeAddr string) error {
	h.mu.Lock()
	savedCb, found := h.savedCallbacks[msg.Body.MessageId]
	delete(h.savedCallbacks, msg.Body.MessageId)
	h.mu.Unlock()

	if found {
		// Send first response from a node back to the user, ignore any other ones.
		// TODO: in practice, we should wait for at least 2F+1 nodes to respond and then return an aggregated response
		// back to the user.
		codec := api.JsonRPCCodec{}
		return savedCb.SendResponse(handlers.UserCallbackPayload{RawResponse: codec.EncodeLegacyResponse(msg), ErrorCode: api.NoError})
	}
	return nil
}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L248-255)
```go
func (h *handler) HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	msg, err := common.ValidatedMessageFromResp(resp)
	if err != nil {
		return err
	}
	if msg.Body.Sender != nodeAddr {
		return errors.New("message sender mismatch when reading from node ")
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```
