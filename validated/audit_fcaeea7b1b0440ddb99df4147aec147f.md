## Analog Found

### Title
Gateway WebAPI handler callback map keyed only by client-supplied `MessageId` allows cross-user response hijacking - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
The vulnerability class in the report is a state-confusion attack where an unprivileged, malicious actor creates a colliding/decoy record that causes the system to misroute or block a legitimate operation belonging to someone else. The Chainlink gateway's WebAPI trigger handler exhibits the same class of bug: the `savedCallbacks` map used to route a node's response back to the correct client is keyed solely by a client-supplied `MessageId`, with no check for an existing, still-pending entry before overwriting it.

### Finding Description
`HandleLegacyUserMessage` stores the caller's callback keyed by `msg.Body.MessageId` without verifying uniqueness against currently pending requests: [1](#0-0) 

The `MessageId` is derived directly from client input (the JSON-RPC request `ID`/legacy message `id`), which is only checked for length/format, not uniqueness or ownership: [2](#0-1) [3](#0-2) 

When a node eventually responds, the handler looks up and deletes the callback purely by `MessageId` and forwards whatever response it finds to whichever callback is currently stored under that key: [4](#0-3) 

The equivalent `dummyHandler` implementation shows the identical unconditional overwrite pattern, confirming this is the intended design rather than an isolated oversight: [5](#0-4) 

If an attacker (or a second concurrent, unprivileged caller) submits a new WebAPI trigger request using the same `MessageId` as an already in-flight, legitimate request on the same DON, the second call's `h.savedCallbacks[msg.Body.MessageId] = ...` silently replaces the first caller's saved callback. This is directly analogous to the report's "dust position" pattern: a low-cost, attacker-created record intentionally collides with/obscures a legitimate one, causing the system that processes both to misbehave — here, the original requester's node response is delivered to the attacker's callback (response leak) or is lost entirely (the original caller hangs until `CallbackMaxAgeSec` and then times out), while the attacker's own request is silently "serviced" by whatever response arrives for the colliding ID.

### Impact Explanation
This allows an unprivileged gateway client to:
- Hijack another in-flight caller's node response (potential disclosure of data intended for a different user/workflow), or
- Deny/disrupt legitimate WebAPI trigger requests by causing them to silently stall until the callback prune timeout.

This matches the "cross-user response confusion" acceptance criterion.

### Likelihood Explanation
Exploitability depends on the attacker being able to predict or race an in-flight `MessageId`. Message IDs are client-chosen strings up to 128 characters with no server-side randomness or namespacing enforced beyond basic format checks, so if any client (external initiator, workflow, or another user of the same DON) can predict or observe another in-flight ID (e.g., sequential/short/test-like IDs, or via timing/race with a known trigger), the collision is straightforward to produce. The concurrency window is the duration of the request round-trip to DON nodes, which is realistic to race in a busy gateway. This is a moderate-likelihood, low-cost issue since it requires no special privilege beyond normal gateway API access.

### Recommendation
Scope `savedCallbacks` keys by sender/session in addition to `MessageId` (e.g., a UUID freshly generated or salted by the gateway itself, or the tuple `(sender-authenticated-identity, MessageId)`), and reject/error on a duplicate `MessageId` from a different sender or during an active callback rather than silently overwriting it.

### Proof of Concept
1. Client A sends a WebAPI trigger request to the gateway with `MessageId = "X"`; the gateway calls `HandleLegacyUserMessage`, storing A's callback under `savedCallbacks["X"]`, and forwards the request to DON nodes.
2. Before a node responds to A's request, Client B (attacker) sends another WebAPI trigger request also using `MessageId = "X"`. The handler unconditionally overwrites `savedCallbacks["X"]` with B's callback (see `core/services/gateway/handlers/capabilities/handler.go:411-420`).
3. When a DON node responds to A's original request (still carrying `MessageId = "X"`), `handleWebAPITriggerMessage` looks up `savedCallbacks["X"]`, finds B's callback, deletes the entry, and delivers A's response to B (`core/services/gateway/handlers/capabilities/handler.go:148-161`).
4. Client A never receives a response and stalls until the callback-prune timeout; Client B receives a response intended for Client A.

Note: I was unable to fully verify from the available index whether upstream code paths always generate a fresh, gateway-controlled `MessageId`/request ID for every legacy WebAPI trigger request or whether some client-facing entry points allow the caller to fully control this ID end-to-end; a Devin session with full repo access would be needed to trace every caller of `HandleLegacyUserMessage`/`HandleJSONRPCUserMessage` to confirm the exact attacker-reachable entry point(s).

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L148-161)
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

**File:** core/services/gateway/api/message.go (L54-66)
```go
func (m *Message) Validate() error {
	if m == nil {
		return errors.New("nil message")
	}
	if len(m.Signature) != MessageSignatureHexEncodedLen {
		return errors.New("invalid hex-encoded signature length")
	}
	if len(m.Body.MessageId) == 0 || len(m.Body.MessageId) > MessageIdMaxLen {
		return errors.New("invalid message ID length")
	}
	if strings.HasSuffix(m.Body.MessageId, NullChar) {
		return errors.New("message ID ending with null bytes")
	}
```

**File:** core/services/gateway/handlers/common/message_util.go (L34-58)
```go
// ValidatedMessageFromReq validated and extracts a legacy Gateway Message
// from params field of JSON-RPC request
func ValidatedMessageFromReq(req *jsonrpc.Request[json.RawMessage]) (*api.Message, error) {
	if req.Version != "2.0" {
		return nil, errors.New("incorrect jsonrpc version")
	}
	if req.Method == "" {
		return nil, errors.New("empty method field")
	}
	if req.Params == nil {
		return nil, errors.New("missing params attribute")
	}
	var m api.Message
	err := json.Unmarshal(*req.Params, &m)
	if err != nil {
		return nil, fmt.Errorf("failed to unmarshal request params: %w", err)
	}
	m.Body.Method = req.Method
	m.Body.MessageId = req.ID
	err = m.Validate()
	if err != nil {
		return nil, err
	}
	return &m, nil
}
```

**File:** core/services/gateway/handlers/handler.dummy.go (L62-109)
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
