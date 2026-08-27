### Title
Cross-user response misdelivery via client-controlled `MessageId` collision in Gateway callback map - (File: core/services/gateway/handlers/handler.dummy.go)

### Summary
The external report describes a fund-recipient confusion: `V3Vault.liquidate()` sends the payout using `msg.sender` instead of the caller-supplied `params.recipient`, so rewards go to the wrong address because the code trusts an implicit identity (the caller) rather than the explicit, validated recipient field. The Chainlink Gateway has a structurally analogous bug: pending-request callbacks are correlated solely by a client-supplied `MessageId` with no binding to the actual requester's identity/session, so a node response can be delivered to the wrong caller when two unprivileged clients pick the same `MessageId`.

### Finding Description
When the Gateway receives a user request, it builds/validates a legacy message and stores the caller's callback keyed only by `msg.Body.MessageId`, a value copied verbatim from the untrusted client request (`req.ID`) with no uniqueness or ownership check: [1](#0-0) 

`gateway.ProcessRequest` only bounds the ID length to 200 characters; it does not enforce uniqueness, does not scope it per-caller, and does not bind it to the connection/session that submitted it: [2](#0-1) 

The `dummyHandler` (illustrative of the handler pattern shared with other gateway handlers, e.g. `capabilities/handler.go`'s `savedCallbacks` map/`handleWebAPITriggerMessage`) stores the caller's `Callback` in a shared map keyed purely by this attacker-controlled `MessageId`: [3](#0-2) 

When a DON node later responds, the handler looks up and invokes whichever callback is currently registered under that same `MessageId`, again with no verification that the responding node's answer actually belongs to the caller who is about to receive it — only that the message parses and the sender matches the expected node address: [4](#0-3) 

Because `savedCallbacks` is a single shared map across all concurrent callers of the handler, and the key space is fully attacker-controlled, two different unprivileged clients issuing requests with the same `MessageId` at overlapping times will collide: the second registration silently overwrites the first entry (`d.savedCallbacks[msg.Body.MessageId] = &savedCallback{...}`). The eventual node response for that ID is then delivered to whichever caller's callback is currently installed — not necessarily the caller whose request produced it. This is the same class of bug as the report: the code uses an unauthenticated/implicit correlation value instead of validating that the response is routed to the entity that is actually entitled to it.

### Impact Explanation
An unprivileged client can cause another unprivileged client's response (which may carry the results of a workflow/target/trigger invocation) to be delivered to itself instead, or cause the legitimate caller's callback to be silently dropped/never invoked (their `Callback.Wait` times out). This is a concrete cross-user response confusion: data belonging to one caller's job/request can be misdirected to an attacker-chosen recipient purely by predicting or guessing another in-flight `MessageId` and racing a request with the same ID, mirroring the "recipient" vs "msg.sender" confusion in the referenced report — the system uses an easily-forged/collidable value as an implicit authorization/routing key.

### Likelihood Explanation
Exploitation requires only sending unauthenticated/ordinary Gateway requests, choosing a `MessageId` and winning a race with another caller's request that uses (or can be predicted to use) the same ID within the callback lifetime (`CallbackMaxAgeSec`, default 120s in `capabilities/handler.go`). No node compromise or operator privilege is required, only the ability to submit HTTP requests to the Gateway's public endpoint. The likelihood of a targeted race is moderate (attacker needs some knowledge/predictability of another client's ID or high enough request volume to feasibly collide within the window), but the underlying design flaw — no per-caller/session binding — makes the class of failure real and directly reachable from an unprivileged client.

### Recommendation
Do not use the bare client-supplied `MessageId` as the sole correlation/authorization key for delivering responses. Bind the saved callback to a server-generated, unpredictable request identifier (or to `MessageId` plus the caller's session/connection identity), reject duplicate/in-flight `MessageId`s from different callers, and verify at delivery time that the response is being routed to the exact caller who created the corresponding pending request rather than to whatever entry currently occupies that map slot.

### Proof of Concept
1. Client A submits a Gateway request with `req.ID = "X"`; the Gateway stores `savedCallbacks["X"] = clientA.callback` per `handler.dummy.go` lines 62-66.
2. Before A's node response arrives, Client B submits a request also using `req.ID = "X"`; this overwrites `savedCallbacks["X"] = clientB.callback`.
3. The node's response intended for Client A's request arrives and is matched via `msg.Body.MessageId == "X"` in `HandleNodeMessage`; since the map entry now points to Client B's callback, Client B receives Client A's response payload (`handler.dummy.go` lines 84-108), while Client A's request either times out or silently never resolves.

### Citations

**File:** core/services/gateway/handlers/common/message_util.go (L46-52)
```go
	var m api.Message
	err := json.Unmarshal(*req.Params, &m)
	if err != nil {
		return nil, fmt.Errorf("failed to unmarshal request params: %w", err)
	}
	m.Body.Method = req.Method
	m.Body.MessageId = req.ID
```

**File:** core/services/gateway/gateway.go (L228-231)
```go
	if len(jsonRequest.ID) > 200 {
		// Arbitrary limit to prevent abuse
		return newError(jsonRequest.ID, api.UserMessageParseError, "request ID is too long: "+strconv.Itoa(len(jsonRequest.ID))+". max is 200 characters")
	}
```

**File:** core/services/gateway/handlers/handler.dummy.go (L62-82)
```go
func (d *dummyHandler) HandleLegacyUserMessage(ctx context.Context, msg *api.Message, callback Callback) error {
	d.mu.Lock()
	d.savedCallbacks[msg.Body.MessageId] = &savedCallback{msg.Body.MessageId, callback}
	don := d.don
	d.mu.Unlock()
	params, err := json.Marshal(msg)
	if err != nil {
		return err
	}
	rawParams := json.RawMessage(params)
	req := &jsonrpc.Request[json.RawMessage]{
		Version: "2.0",
		ID:      msg.Body.MessageId,
		Method:  msg.Body.Method,
		Params:  &rawParams,
	}
	for _, member := range d.donConfig.Members {
		err = errors.Join(err, don.SendToNode(ctx, member.Address, req))
	}
	return err
}
```

**File:** core/services/gateway/handlers/handler.dummy.go (L84-109)
```go
func (d *dummyHandler) HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	var msg api.Message
	err := json.Unmarshal(*resp.Result, &msg)
	if err != nil {
		return err
	}
	msg.Body.MessageId = resp.ID
	err = msg.Validate()
	if err != nil {
		return err
	}
	if nodeAddr != msg.Body.Sender {
		return fmt.Errorf("node address %s does not match message sender %s", nodeAddr, msg.Body.Sender)
	}
	d.mu.Lock()
	savedCb, found := d.savedCallbacks[msg.Body.MessageId]
	delete(d.savedCallbacks, msg.Body.MessageId)
	d.mu.Unlock()

	if found {
		// Send first response from a node back to the user, ignore any other ones.
		codec := api.JsonRPCCodec{}
		return savedCb.SendResponse(UserCallbackPayload{RawResponse: codec.EncodeLegacyResponse(&msg), ErrorCode: api.NoError})
	}
	return nil
}
```
