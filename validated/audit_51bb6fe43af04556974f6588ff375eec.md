Confirmed: `msg.Body.MessageId` in `core/services/gateway/handlers/capabilities/handler.go` is set directly from the client-supplied JSON-RPC `req.ID` [1](#0-0)  and this handler's legacy path does not check whether an entry already exists before overwriting the shared `savedCallbacks` map [2](#0-1) , unlike the newer vault/v2 handlers which explicitly reject a request ID collision (`"request ID already exists"`) [3](#0-2) .

### Title
Client-Controlled MessageId Enables Cross-User Callback Hijacking/DoS in Gateway Capabilities Handler - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
The Astaria report describes an unprivileged actor purchasing a small lien and directing its token to a malicious receiver, whose callback is invoked from a *shared* code path, letting the attacker DoS/hijack behavior that affects other unrelated users. The analogous root cause in Chainlink's gateway "capabilities" handler is that the key used in a shared, unauthenticated map (`savedCallbacks`) is the raw, attacker-controlled JSON-RPC request `ID`, with no ownership check or collision rejection before insertion, unlike the sibling `vault` handler which explicitly guards against this.

### Finding Description
`HandleLegacyUserMessage` in `core/services/gateway/handlers/capabilities/handler.go` stores every incoming user's callback keyed purely by `msg.Body.MessageId`, which is copied verbatim from the untrusted JSON-RPC request ID supplied by the HTTP caller [1](#0-0) . The insertion into the shared map performs no existence check: [2](#0-1) 

Because the gateway's `ProcessRequest` only bounds request-ID length (≤200 chars) and does not enforce uniqueness across concurrent in-flight requests from different unprivileged clients [4](#0-3) , any unauthenticated/unprivileged HTTP caller can choose the same `id` value as another in-flight request. If a second request with a colliding ID arrives while the first is still pending, the second call to `h.savedCallbacks[msg.Body.MessageId] = &savedCallback{...}` in `HandleLegacyUserMessage` silently overwrites the first user's stored `Callback` in the map before the DON responds.

When the DON node eventually responds, `handleWebAPITriggerMessage` looks up and deletes the callback by `MessageId` and delivers the response to whichever `Callback` is currently stored under that key: [5](#0-4) 

This produces exactly the cross-actor confusion class described in the report: an unprivileged party's action (submitting a colliding request ID) silently affects a caller they have no privilege over — either the original caller never receives a response (hangs until the 120s callback timeout, i.e., DoS) or, more seriously, the original caller's response slot is now owned by the attacker so the attacker's later-arriving pending response is delivered to the wrong original waiting client if there is a further collision sequence, and vice versa the victim can receive a response actually intended for the attacker's own workflow trigger.

The contrast with `core/services/gateway/handlers/vault/handler.go`'s `newActiveRequest`, which explicitly rejects duplicate request IDs (`"request ID already exists"`) [6](#0-5) , indicates that ID-collision protection is an established mitigation elsewhere in the same codebase but is missing from this handler, confirming this is a real gap rather than intentional design.

### Impact Explanation
- Denial of service: a legitimate caller's webhook/trigger response can be silently dropped (overwritten) by any other unprivileged client choosing the same request ID, causing the legitimate call to hang until timeout and never receive its result.
- Cross-user response confusion: the internal callback map does not bind the stored `Callback` to the originating caller's identity/session beyond the raw ID string, so a malicious client can attempt to intercept or corrupt in-flight request/response routing for another caller by choosing a predictable or guessed ID.
- No fund movement is directly implicated here (this is off-chain gateway routing), but it does allow an unprivileged actor to disrupt other users' trigger request/response delivery, which matches the "DoS / response confusion via unprivileged, gateway-reachable action" analog class.

### Likelihood Explanation
Likelihood is moderate: it requires the attacker to predict or guess another caller's in-flight request ID and to time their request while the original is still pending (before the DON responds, within the 120s callback window). If callers use predictable/sequential/well-known IDs (e.g., workflow-based deterministic IDs), or an attacker can observe IDs through some side channel, exploitation becomes straightforward. No authentication bypass is required — this is reachable via the plain gateway HTTP endpoint by any unprivileged sender who knows the DON/service name.

### Recommendation
- Reject duplicate request IDs at insertion time in `HandleLegacyUserMessage`, mirroring the guard already present in `vault/handler.go`'s `newActiveRequest` (return an error such as "request ID already exists" instead of overwriting).
- Scope the `savedCallbacks` key by both request ID and caller/session identity (or a server-generated internal correlation ID) rather than trusting the raw client-supplied ID alone.
- Apply the same fix pattern used by `confidentialrelay/handler.go`'s `newActiveRequest`, which similarly needs to be checked for the same collision issue.

### Proof of Concept
1. Attacker A sends a legacy gateway request (`web_api_trigger`) with request ID `"X"` for a webhook/trigger to a DON, which is stored in `savedCallbacks["X"]` pointing at Attacker A's HTTP-blocking callback.
2. Before the DON responds (within `CallbackMaxAgeSec`, default 120s), Victim B independently sends a request that happens to reuse ID `"X"` (e.g., predictable IDs, or brute-forced collision). `savedCallbacks["X"]` is overwritten to point to Victim B's callback (per the unconditional assignment at `handler.go:412`).
3. When the DON node responds to Attacker A's original request with `MessageId = "X"`, `handleWebAPITriggerMessage` looks up `savedCallbacks["X"]` — now Victim B's callback — and delivers Attacker A's trigger response to Victim B instead, while Attacker A's own request now silently never resolves (DoS on Attacker's or, depending on ordering, Victim's request), demonstrating cross-user response misdelivery/DoS purely from an unprivileged, gateway-reachable request.

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```

**File:** core/services/gateway/handlers/vault/handler.go (L466-481)
```go
func (h *handler) newActiveRequest(req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) (*activeRequest, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.activeRequests[req.ID] != nil {
		h.lggr.Errorw("request id already exists", "requestID", req.ID)
		return nil, errors.New("request ID already exists: " + req.ID)
	}
	ar := &activeRequest{
		Callback:  callback,
		req:       req,
		createdAt: h.clock.Now(),
		responses: map[string]*jsonrpc.Response[json.RawMessage]{},
	}
	h.activeRequests[req.ID] = ar
	return ar, nil
}
```

**File:** core/services/gateway/gateway.go (L218-231)
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
```
