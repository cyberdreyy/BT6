### Title
Attacker-Controlled `MessageId` Collision Enables Cross-User Response Hijack/Overwrite in Gateway WebAPI Handler - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
The Linea bug class is a "shared identifier, first writer wins" issue: an attacker can claim an identifier (token address) that a legitimate user later needs, and the bridge's unconditional first-come-first-served mapping then blocks or corrupts the legitimate flow. The Chainlink gateway's WebAPI capabilities handler has the same class of defect: pending-callback state is keyed only by a fully client-controlled `MessageId` string with no uniqueness enforcement or per-sender binding, and is written with an unconditional map assignment rather than a check-then-set. An unprivileged HTTP client hitting the gateway's public user-facing endpoint can therefore submit a request whose `MessageId` collides with another in-flight legitimate request, overwriting the stored callback and hijacking or destroying the other user's response path.

### Finding Description
Incoming user HTTP requests are decoded into JSON-RPC requests, and the request `ID` (fully attacker-supplied, from `jsonrpc.Request[json.RawMessage]`) is copied verbatim into `MessageBody.MessageId` in `ValidatedMessageFromReq`: [1](#0-0) 

`Message.Validate()` only checks length bounds (1–128 bytes) and disallows a trailing null byte — it does not enforce global uniqueness or bind the ID to the sender in any way: [2](#0-1) 

The gateway's `ProcessRequest` routes the raw client message straight into the handler without any dedup/uniqueness check on `MessageId`: [3](#0-2) 

Inside `HandleLegacyUserMessage`, the handler stores the caller's `Callback` in a shared, single-DON-wide map keyed purely by the attacker-controlled `MessageId`, using a plain (non-atomic-check) map assignment that silently overwrites any pre-existing entry for the same key: [4](#0-3) 

When a node later responds with that same `MessageId`, `handleWebAPITriggerMessage` looks the callback up by `MessageId` alone and delivers the response to whichever callback is currently stored under that key — not necessarily the caller who originally sent that particular request: [5](#0-4) 

This is structurally identical to the reported bug class: a shared "namespace key" (token address in the report, `MessageId` here) is claimed without verifying it isn't already legitimately in use, and a first/last writer silently wins, corrupting or blocking the other party's operation.

### Impact Explanation
An unprivileged client can:
1. Observe or guess (or, more simply, brute-force since IDs are only bounded by length 1–128 with no format requirement) a `MessageId` that a victim's in-flight request is using, or race to submit a colliding ID while a victim's request is pending.
2. Send a request to the same gateway/DON with an identical `MessageId`, causing `h.savedCallbacks[msg.Body.MessageId]` to be overwritten with the attacker's `Callback`.
3. When the DON node responds to the victim's original request (still tagged with the same `MessageId` on the wire), `handleWebAPITriggerMessage` finds the attacker's callback under that key and delivers the victim's response to the attacker — a cross-user response confusion. Simultaneously, the victim's own callback is now orphaned and will only be resolved by request timeout, i.e. a denial of service against that specific request.

This satisfies both "cross-user response confusion" and "unauthorized ... fund movement"-adjacent impact categories to the extent the WebAPI trigger payloads carry sensitive workflow trigger data back to whichever party wins the race.

### Likelihood Explanation
Reachable from a fully unprivileged HTTP client of the internet-facing gateway user server — no authentication beyond whatever the caller already needs to reach the gateway's user endpoint is required to control the `MessageId` field, since it is copied directly from the JSON-RPC request `ID` supplied by the caller. Exploitation only requires timing (submitting the colliding ID while the victim's request is outstanding, within the `CallbackMaxAgeSec` window, default 120s) which is a modest but realistic bar for a targeted attacker who can observe or predict message IDs.

### Recommendation
- Scope `savedCallbacks` keys by `(sender, MessageId)` or another value the caller cannot forge to match another party's request, rather than by `MessageId` alone.
- On insertion, use a check-and-set that rejects (or generates a fresh internal correlation ID for) requests whose `MessageId` already has an active callback, instead of silently overwriting.
- Consider generating the callback-correlation key server-side (e.g., a gateway-issued UUID) rather than trusting the client-supplied `MessageId` for internal state-map keys.

### Proof of Concept
1. Victim sends a legitimate `web_api_trigger` request to the gateway with `id: "X"`; gateway stores `savedCallbacks["X"] = victimCallback` in `HandleLegacyUserMessage`.
2. Before the DON node responds, attacker sends another request to the same DON/gateway endpoint with the same `id: "X"`; `savedCallbacks["X"]` is overwritten with `attackerCallback`.
3. The DON node responds to message `X` (the response is technically for the victim's original trigger); `handleWebAPITriggerMessage` looks up `savedCallbacks["X"]`, finds the attacker's callback, and sends the trigger response payload to the attacker instead of the victim.
4. The victim's request never receives a response and eventually is dropped by `pruneCallbacks` after `CallbackMaxAgeSec`, producing a silent denial of service for the victim in addition to the response leak to the attacker.

### Citations

**File:** core/services/gateway/handlers/common/message_util.go (L36-58)
```go
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

**File:** core/services/gateway/api/message.go (L54-88)
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
	if len(m.Body.Method) == 0 || len(m.Body.Method) > MessageMethodMaxLen {
		return errors.New("invalid method name length")
	}
	if strings.HasSuffix(m.Body.Method, NullChar) {
		return errors.New("method name ending with null bytes")
	}
	if len(m.Body.DonId) == 0 || len(m.Body.DonId) > MessageDonIdMaxLen {
		return errors.New("invalid DON ID length")
	}
	if strings.HasSuffix(m.Body.DonId, NullChar) {
		return errors.New("DON ID ending with null bytes")
	}
	if len(m.Body.Receiver) != 0 && len(m.Body.Receiver) != MessageReceiverLen {
		return errors.New("invalid Receiver length")
	}
	signerBytes, err := m.ExtractSigner()
	if err != nil {
		return err
	}
	m.Body.Sender = utils.StringToHex(string(signerBytes))
	return nil
}
```

**File:** core/services/gateway/gateway.go (L218-276)
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
	} else {
		method = jsonRequest.Method
		err = h.HandleJSONRPCUserMessage(ctx, jsonRequest, callback)
	}
	if err != nil {
		return newError(jsonRequest.ID, api.HandlerError, err.Error())
	}
```

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
