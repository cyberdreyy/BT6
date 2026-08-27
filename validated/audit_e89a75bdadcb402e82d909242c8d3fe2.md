### Title
Cross-user response confusion via unkeyed `savedCallbacks` map on `MessageId` collision - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.HandleLegacyUserMessage` stores each incoming web-API-trigger request's callback keyed only by `msg.Body.MessageId`, with no check for an existing entry and no binding to the requester's identity (sender address). [1](#0-0)  Since `MessageId` is attacker-controlled (it is the JSON-RPC request `ID` supplied by the caller and copied verbatim into `msg.Body.MessageId`), an unprivileged client can submit a request whose `MessageId` collides with another user's in-flight request, silently overwriting that user's saved callback so the DON's later response is delivered to the attacker instead of the rightful requester.

### Finding Description
The gateway's `ProcessRequest` decodes the incoming JSON-RPC request and, for legacy requests, calls `h.HandleLegacyUserMessage(ctx, msg, callback)` where `msg.Body.MessageId` is set directly from the caller-supplied JSON-RPC `ID` field (`msg.Body.MessageId = req.ID` via `ValidatedMessageFromReq`, and no association with `msg.Body.DonId`/sender is required beyond a 200-char length cap). [2](#0-1) [3](#0-2) 

Inside `HandleLegacyUserMessage`, after validation, the handler stores the callback keyed purely by `msg.Body.MessageId`:
```go
h.mu.Lock()
h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
don := h.don
h.mu.Unlock()
``` [1](#0-0) 

There is no check for an existing key before overwriting it, unlike the sibling `RequestCache` implementation used elsewhere in the gateway, which keys pending requests by `globalId{sender, id}` and explicitly rejects duplicates with `"request already exists"`. [4](#0-3)  Because `savedCallbacks` is keyed only by the raw `MessageId` string (no sender component), two different unprivileged clients (or the same client racing itself) can send two `HandleLegacyUserMessage` requests with identical `MessageId`s. Both requests independently pass signature validation (`msg.Validate()` only checks the signature is well-formed and derives `Sender` from it — it does not check `MessageId` uniqueness or bind it to a specific sender). [5](#0-4) 

When the DON later replies, `HandleNodeMessage` → `handleWebAPITriggerMessage` looks up and deletes the entry solely by `msg.Body.MessageId` and invokes whatever `savedCallback` is currently stored, delivering the response to whichever caller most recently won the write race:
```go
h.mu.Lock()
savedCb, found := h.savedCallbacks[msg.Body.MessageId]
delete(h.savedCallbacks, msg.Body.MessageId)
h.mu.Unlock()
if found {
    return savedCb.SendResponse(...)
}
``` [6](#0-5) 

The only sender check performed (`msg.Body.Sender != nodeAddr`) validates that the DON node itself is a legitimate node, not that the response belongs to the caller who originally submitted the request. [7](#0-6)  Thus the first user's (potentially sensitive, e.g. containing secrets/results from workflow execution) response is delivered to the second (attacker) HTTP caller.

### Impact Explanation
This is a cross-user response confusion / request impersonation bug: an unprivileged gateway client can hijack another user's pending web-API-trigger response by colliding on `MessageId`, receiving data intended for someone else. Depending on the workflow's trigger payload/response contents, this can leak query results or other response data belonging to a different, unrelated caller — a confidentiality violation and request-binding bypass at the gateway boundary.

### Likelihood Explanation
Exploitation requires only unauthenticated/unprivileged access to the gateway's public JSON-RPC endpoint that routes to this DON/handler (`Methods()` includes `MethodWebAPITrigger`). [8](#0-7)  The attacker must know or guess the victim's `MessageId` (i.e., win a race with a plausible/predictable or observed ID) and submit their own signed request with that same ID before the DON's reply for the victim's request arrives — a narrow but real timing window, especially if IDs are predictable, sequential, or observable to the attacker (e.g., through a shared client library defaulting to counters, or leaked via other channels). No special role or credential is needed beyond the ability to sign a well-formed request with the attacker's own key.

### Recommendation
Bind `savedCallbacks` entries to both sender identity and MessageId, mirroring `RequestCache`'s `globalId{sender, id}` keying, and reject (or otherwise safely handle) a request whose composite key already exists rather than silently overwriting the outstanding entry. Also validate on `handleWebAPITriggerMessage` that the response's originating context matches the same composite key used at insertion time.

### Proof of Concept
```go
func TestHandler_SavedCallbacksOverwriteBySameMessageId(t *testing.T) {
    handler, _, don, nodes := setupHandler(t)
    ctx := t.Context()
    don.On("SendToNode", mock.Anything, mock.Anything, mock.Anything).Return(nil)

    // Victim request, MessageId "12345", signed by nodes[0] key
    victimMsg := triggerRequest(t, victimKey, []string{"topic"}, "", "", "")
    victimCb := hc.NewCallback()
    require.NoError(t, handler.HandleLegacyUserMessage(ctx, victimMsg, victimCb))

    // Attacker crafts own request reusing the same MessageId "12345", signed by attackerKey
    attackerMsg := triggerRequestWithId(t, attackerKey, "12345", ...)
    attackerCb := hc.NewCallback()
    require.NoError(t, handler.HandleLegacyUserMessage(ctx, attackerMsg, attackerCb))

    // DON responds once to MessageId "12345"
    resp, _ := hc.ValidatedResponseFromMessage(victimMsg)
    require.NoError(t, handler.HandleNodeMessage(ctx, resp, nodes[0].Address))

    // Expected (secure) behavior: victimCb receives the response
    // Actual (vulnerable) behavior: attackerCb.Wait returns the response instead,
    // and victimCb.Wait times out / never resolves.
    r, err := attackerCb.Wait(ctx) // demonstrates leak to attacker
    require.NoError(t, err)
    require.Equal(t, codec.EncodeLegacyResponse(victimMsg), r.RawResponse)
}
```
This test currently would pass on the vulnerable code (attacker receives victim's response) demonstrating cross-user confusion caused by the unguarded overwrite at line 412.

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

**File:** core/services/gateway/handlers/capabilities/handler.go (L239-246)
```go
func (h *handler) Methods() []string {
	return []string{
		MethodWebAPITrigger,
		MethodWebAPITarget,
		MethodComputeAction,
		MethodWorkflowSyncer,
	}
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

**File:** core/services/gateway/handlers/common/requestcache.go (L34-63)
```go
type globalId struct {
	sender string
	id     string
}

type pendingRequest[T any] struct {
	handlers.Callback
	responseData *T
	timeoutTimer *time.Timer
	mu           sync.Mutex
}

func NewRequestCache[T any](timeout time.Duration, maxCacheSize uint32) RequestCache[T] {
	return &requestCache[T]{cache: make(map[globalId]*pendingRequest[T]), timeout: timeout, maxCacheSize: maxCacheSize}
}

func (c *requestCache[T]) NewRequest(lggr logger.Logger, request *api.Message, callback handlers.Callback, responseData *T) error {
	if request == nil {
		return errors.New("request is nil")
	}
	if responseData == nil {
		return errors.New("responseData is nil")
	}
	key := globalId{request.Body.Sender, request.Body.MessageId}
	c.mu.Lock()
	defer c.mu.Unlock()
	_, ok := c.cache[key]
	if ok {
		return errors.New("request already exists")
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
