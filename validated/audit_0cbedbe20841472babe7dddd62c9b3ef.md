Based on my analysis, this is a genuine cross-user response confusion vulnerability.

### Title
Attacker-supplied `MessageId` collision in `dummyHandler` allows hijacking another user's `savedCallback` response - ([File: core/services/gateway/handlers/handler.dummy.go])

### Summary
`dummyHandler.HandleLegacyUserMessage` stores callbacks in a single map keyed only by the caller-controlled `msg.Body.MessageId`, with no binding to the requester's identity. Any unprivileged user submitting a legacy/JSON-RPC user message can choose the same `MessageId` as a victim's pending request, silently overwriting the victim's saved callback so that the node's eventual response is delivered to the attacker instead of the victim.

### Finding Description
`HandleLegacyUserMessage` writes `d.savedCallbacks[msg.Body.MessageId] = &savedCallback{...}` [1](#0-0)  using only `msg.Body.MessageId` as the key, with no per-sender namespacing. `MessageId` is part of `MessageBody` supplied by the caller and is only checked for length/format in `Validate()` — it is not required to be globally unique or bound to `Sender` [2](#0-1) . `HandleJSONRPCUserMessage` derives `MessageId` from `jsonRequest.ID`, which is also attacker/caller-supplied [3](#0-2) .

When `HandleNodeMessage` later processes a node response, it looks up the callback purely by `msg.Body.MessageId` [4](#0-3) , and the only sender-binding check present (`nodeAddr != msg.Body.Sender`) validates the *node* that authored the response, not which *user* originally submitted the request. There is no field tying a `savedCallback` back to the original caller's identity/signature.

Exploit flow: victim signs and submits `HandleLegacyUserMessage` with `MessageId = "shared-id"`, callback A is stored. Before a node responds, attacker (a normal unprivileged gateway user, needs only to be able to sign a valid message per `Validate()`) submits another `HandleLegacyUserMessage` with the same `MessageId = "shared-id"`, causing callback B to overwrite callback A in the shared map. When any node's response for `"shared-id"` arrives, `HandleNodeMessage` finds only callback B and invokes `SendResponse` on it — delivering the response to the attacker's HTTP connection, while the victim's original request silently never gets a response (effectively hangs or times out at the outer request layer).

The `nodeAddr != msg.Body.Sender` check only stops a malicious node from impersonating another node; it does nothing to prevent unrelated users from colliding on `MessageId`.

### Impact Explanation
This is cross-user response confusion: an unprivileged attacker can capture the DON's node response payload intended for another user's request, and can also silently disrupt the delivery of that response to the legitimate caller (denial of a specific request). Depending on what payloads the `dummy` handler is used for (e.g., generic "dummy"/pass-through routing per `handler_factory.go`'s `DummyHandlerType`), this could leak node-computed response data to an attacker who never made the underlying request, or effectively deny service to a specific victim by consuming their intended reply.

### Likelihood Explanation
Preconditions are minimal: the attacker needs only to be a legitimate unprivileged user of the gateway capable of submitting a signed legacy/JSON-RPC message (same trust level as any normal caller) — no DON, node, or admin privileges required. The attacker must guess or otherwise learn the victim's `MessageId` and race their own submission before the node responds; `MessageId` is caller-chosen and not randomized/validated for uniqueness or unpredictability by the gateway, so if an attacker can predict or observe a victim's `MessageId` (e.g., sequential IDs, replayed IDs, or IDs echoed back to the requester over an insecure channel), the race is straightforward and repeatable.

### Recommendation
Bind `savedCallbacks` keys (or a companion field on `savedCallback`) to the original caller's identity/signature (e.g., a key derived from `(Sender, MessageId)` rather than `MessageId` alone), and reject/collision-check duplicate `MessageId` submissions from the same or different senders. Additionally, verify in `HandleNodeMessage` that the response's `Sender`/`Receiver` metadata matches the same identity that originally submitted the request tied to that callback.

### Proof of Concept
Go unit test in `core/services/gateway/handlers/handler.dummy_test.go`:
1. Construct two `Callback` mocks, `victimCb` and `attackerCb` (using existing `handlermocks.Callback`).
2. Build two `api.Message` values with identical `Body.MessageId = "shared-id"` but different (valid) signers/`Sender` values (sign with two different ECDSA keys via `Message.Sign`).
3. Call `dummyHandler.HandleLegacyUserMessage(ctx, victimMsg, victimCb)`, then `dummyHandler.HandleLegacyUserMessage(ctx, attackerMsg, attackerCb)` on the same handler instance.
4. Construct a `jsonrpc.Response` with `ID = "shared-id"` and a `Result` payload whose embedded `api.Message.Body.Sender` matches a legitimate DON node address, and call `HandleNodeMessage`.
5. Assert `attackerCb.SendResponse` was called (mock expectation set) and `victimCb.SendResponse` was NOT called, demonstrating the victim's callback was silently dropped and the attacker's callback received the response — confirming the isolation invariant is violated.

### Citations

**File:** core/services/gateway/handlers/handler.dummy.go (L45-59)
```go
func (d *dummyHandler) HandleJSONRPCUserMessage(ctx context.Context, jsonRequest jsonrpc.Request[json.RawMessage], callback Callback) error {
	var msg api.Message
	if jsonRequest.Params != nil {
		if err := json.Unmarshal(*jsonRequest.Params, &msg); err != nil {
			return err
		}
	}
	msg.Body.MessageId = jsonRequest.ID
	if msg.Body.Method == "" {
		msg.Body.Method = jsonRequest.Method
	}
	if msg.Body.DonId == "" {
		msg.Body.DonId = d.donConfig.DonId
	}
	return d.HandleLegacyUserMessage(ctx, &msg, callback)
```

**File:** core/services/gateway/handlers/handler.dummy.go (L62-66)
```go
func (d *dummyHandler) HandleLegacyUserMessage(ctx context.Context, msg *api.Message, callback Callback) error {
	d.mu.Lock()
	d.savedCallbacks[msg.Body.MessageId] = &savedCallback{msg.Body.MessageId, callback}
	don := d.don
	d.mu.Unlock()
```

**File:** core/services/gateway/handlers/handler.dummy.go (L98-106)
```go
	d.mu.Lock()
	savedCb, found := d.savedCallbacks[msg.Body.MessageId]
	delete(d.savedCallbacks, msg.Body.MessageId)
	d.mu.Unlock()

	if found {
		// Send first response from a node back to the user, ignore any other ones.
		codec := api.JsonRPCCodec{}
		return savedCb.SendResponse(UserCallbackPayload{RawResponse: codec.EncodeLegacyResponse(&msg), ErrorCode: api.NoError})
```

**File:** core/services/gateway/api/message.go (L61-66)
```go
	if len(m.Body.MessageId) == 0 || len(m.Body.MessageId) > MessageIdMaxLen {
		return errors.New("invalid message ID length")
	}
	if strings.HasSuffix(m.Body.MessageId, NullChar) {
		return errors.New("message ID ending with null bytes")
	}
```
