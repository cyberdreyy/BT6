### Title
`Message.Validate()` only rejects trailing null bytes in `MessageId`/`Method`/`DonId`, allowing embedded null-byte payloads to pass envelope validation - ([File: core/services/gateway/api/message.go])

### Summary
`Message.Validate()` in `core/services/gateway/api/message.go` checks for null bytes only with `strings.HasSuffix(..., NullChar)`, so a `MessageId`, `Method`, or `DonId` containing an embedded `\x00` anywhere except the very end passes validation. Because these fields are subsequently used verbatim as map keys, signed payload components, and log fields across the gateway/connector code, an attacker can craft envelopes whose full identifier differs from what gets displayed/logged when consumers truncate or render the string at the first null byte.

### Finding Description
`Message.Validate()` performs these checks on the three identifier fields: [1](#0-0) 

Each check only calls `strings.HasSuffix(field, NullChar)` — it never scans for a null byte occurring anywhere inside the string (e.g. `strings.Contains`). This is confirmed by the accompanying unit test, which only exercises trailing-null cases (`"myid\x00\x00"`, `"mydon\x00\x00"`, `"method\x00"`) and never an embedded case such as `"abc\x00def"`: [2](#0-1) 

A crafted `MessageId` such as `"victim-msg-id\x00attacker-suffix"` therefore satisfies `Validate()` (length within bounds, no *trailing* null) and flows unmodified into:
- Map keys used for request/response correlation and idempotency, e.g. `h.savedCallbacks[msg.Body.MessageId]` and `h.callbacks[requestID]` in the capabilities and v2 HTTP trigger handlers. [3](#0-2) [4](#0-3) 
- Structured log fields (`"messageId"`, `"requestID"`) emitted via the logger, which is where log/terminal/JSON viewers commonly truncate display at the first NUL byte, causing the operator-visible identifier to look identical to a different, legitimate message ID while the underlying full string (and its map-key/signature identity) is the attacker's distinct value. [5](#0-4) 

Because Go string/byte-slice comparisons are exact (not null-terminated), the embedded-null string does not collide as a map key or in `GetRawMessageBody`'s fixed-length copy/signature construction — so this is not a functional/auth bypass of the lookup logic itself. The exploitable soundness gap is that `Validate()`'s intent ("no embedded control/null bytes in identifiers") is only partially enforced, letting a value that *displays/logs* as one identifier actually *be* a different identifier internally, which is precisely the invariant the question targets.

### Impact Explanation
This enables request/log confusion: two distinct envelopes (one from a legitimate sender, one attacker-crafted with an embedded null plus arbitrary suffix truncated in display) can appear identical in logs, dashboards, or any downstream consumer that renders C-style/null-terminated strings, while remaining distinct in the system's actual routing/authorization state (map keys). This maps to a "cross-user response confusion" / audit-trail integrity impact class rather than a direct auth bypass, since map-based routing itself is unaffected by the embedded null (Go strings aren't null-terminated for equality checks).

### Likelihood Explanation
Low privilege required: any unauthenticated/gateway client able to send a signed `api.Message` (or, for legacy JSON-RPC requests, any client able to set `req.ID`/`Body.MessageId`) can set an embedded null byte, since `Validate()` never rejects it. The attack is trivially repeatable — it only requires crafting a string with `\x00` not at the final position.

### Recommendation
In `Message.Validate()` (`core/services/gateway/api/message.go`), replace the `strings.HasSuffix(field, NullChar)` checks with `strings.Contains(field, NullChar)` for `MessageId`, `Method`, and `DonId`, rejecting any embedded null byte, not just a trailing one.

### Proof of Concept
Add a table-driven test to `core/services/gateway/api/message_test.go`:
```go
func TestMessage_Validate_RejectsEmbeddedNullBytes(t *testing.T) {
    base := func() *api.Message {
        return &api.Message{Body: api.MessageBody{
            MessageId: "abcd", Method: "request", DonId: "donA",
            Receiver: "0x0000000000000000000000000000000000000000",
            Payload:  []byte("data"),
        }}
    }
    cases := []struct{ name, id, method, donID string }{
        {"embedded null in MessageId", "abc\x00def", "request", "donA"},
        {"embedded null in Method", "abcd", "meth\x00od", "donA"},
        {"embedded null in DonId", "abcd", "request", "don\x00A"},
    }
    for _, tc := range cases {
        t.Run(tc.name, func(t *testing.T) {
            msg := base()
            msg.Body.MessageId, msg.Body.Method, msg.Body.DonId = tc.id, tc.method, tc.donID
            pk, err := crypto.GenerateKey()
            require.NoError(t, err)
            require.NoError(t, msg.Sign(pk))
            err = msg.Validate() // expected to fail; currently passes
            require.Error(t, err, "Validate must reject embedded null bytes, not just trailing ones")
        })
    }
}
```
Expected (post-fix) assertion: `Validate()` returns an error for all three cases. Currently, this PoC demonstrates `Validate()` returns `nil` for embedded-null identifiers, confirming the gap.

### Citations

**File:** core/services/gateway/api/message.go (L61-78)
```go
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
```

**File:** core/services/gateway/api/message_test.go (L34-59)
```go
	// message ID ending with null bytes
	msg.Body.MessageId = "myid\x00\x00"
	require.Error(t, msg.Validate())
	msg.Body.MessageId = "abcd"
	require.NoError(t, msg.Validate())

	// missing DON ID
	msg.Body.DonId = ""
	require.Error(t, msg.Validate())
	// DON ID ending with null bytes
	msg.Body.DonId = "mydon\x00\x00"
	require.Error(t, msg.Validate())
	msg.Body.DonId = "donA"
	require.NoError(t, msg.Validate())

	// method name too long
	msg.Body.Method = string(bytes.Repeat([]byte("a"), api.MessageMethodMaxLen+1))
	require.Error(t, msg.Validate())
	// empty method name
	msg.Body.Method = ""
	require.Error(t, msg.Validate())
	// method name ending with null bytes
	msg.Body.Method = "method\x00"
	require.Error(t, msg.Validate())
	msg.Body.Method = "request"
	require.NoError(t, msg.Validate())
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L164-166)
```go
func (h *handler) handleWebAPIOutgoingMessage(ctx context.Context, msg *api.Message, nodeAddr string) error {
	h.lggr.Debugw("handling webAPI outgoing message", "messageId", msg.Body.MessageId, "nodeAddr", nodeAddr)
	if !h.nodeRateLimiter.Allow(nodeAddr) {
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-413)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L398-405)
```go
func (h *httpTriggerHandler) setupCallback(ctx context.Context, requestID string, callback handlers.Callback, requestStartTime time.Time, workflowID string) (<-chan struct{}, error) {
	h.callbacksMu.Lock()
	defer h.callbacksMu.Unlock()

	if _, found := h.callbacks[requestID]; found {
		h.handleUserError(ctx, requestID, jsonrpc.ErrConflict, fmt.Sprintf("requestID: %s has already been used. Ensure the requestID is unique for each request.", requestID), callback)
		return nil, fmt.Errorf("in-flight request ID: %s", requestID)
	}
```
