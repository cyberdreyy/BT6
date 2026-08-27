### Title
Missing sender allowlist check allows arbitrary signer to trigger DON-wide workflow execution - (`core/services/gateway/handlers/capabilities/handler.go`)

### Summary
`handler.HandleLegacyUserMessage` never validates `msg.Body.Sender` against any allowlist of authorized triggerers before caching a callback and broadcasting the request to every DON member via `don.SendToNode`. The only gate before this handler, `Message.Validate()` in the gateway's `ProcessRequest`, merely checks message format and recovers a valid ECDSA signer — it does not require that signer be a known/authorized entity.

### Finding Description
The request path is: `gateway.ProcessRequest` (`core/services/gateway/gateway.go:250-269`) decodes the incoming JSON, calls `msg.Validate()` which only checks field lengths/format and calls `ExtractSigner()` to populate `msg.Body.Sender` from the ECDSA signature [1](#0-0) , then dispatches to `h.HandleLegacyUserMessage(ctx, msg, callback)` [2](#0-1) .

Inside `HandleLegacyUserMessage`, the function validates payload decoding, checks `payload.Timestamp` staleness, and checks the method is `MethodWebAPITrigger` — but at no point does it compare `msg.Body.Sender` (or any signer-derived identity) against `h.donConfig.Members` or any subscriber allowlist. The code explicitly marks this gap with `// TODO: apply allowlist and rate-limiting here` [3](#0-2) . After these checks pass, the handler saves a callback keyed by `msg.Body.MessageId` and broadcasts the request to every DON member: [4](#0-3) .

Since `Message.Validate()` accepts any syntactically valid signature from any keypair (it doesn't check membership) [1](#0-0) , and `HandleLegacyUserMessage` performs no additional sender authorization, any attacker who can generate an ECDSA keypair and sign a well-formed `MessageBody` with `Method=MethodWebAPITrigger` and a fresh `Timestamp` can have their message routed to, and executed by, every node in the target DON. The handler's own test suite acknowledges this gap with the comment `// TODO: Validate Senders and rate limit check, pending question in trigger about where senders and rate limits are validated` [5](#0-4) , confirming there is no other layer (middleware, node-side check) currently enforcing this invariant for the legacy trigger path.

### Impact Explanation
This breaks the "authorization exactness" invariant that only allowlisted subscribers may trigger DON workflow execution. An unauthenticated external attacker with just a freshly generated ECDSA key can cause `web_api_trigger` messages to be sent to and processed by every node in the DON's member set, resulting in unauthorized triggering of workflow executions across the whole DON — matching the Chainlink bounty impact class of unauthorized job/workflow run triggering via authorization bypass.

### Likelihood Explanation
Preconditions are minimal: no credentials, no DON registration, no special role — just the ability to generate a keypair (trivial) and craft a JSON-RPC-formatted `MessageBody` with `Method=MethodWebAPITrigger` and a non-stale `Timestamp`, signed with that key, and POST it to the gateway's HTTP endpoint feeding `ProcessRequest`. This is fully repeatable and requires no race condition or timing dependency; it works deterministically as coded, since the check that would prevent it is explicitly marked "TODO" and unimplemented.

### Recommendation
In `HandleLegacyUserMessage`, before broadcasting to DON members, verify `msg.Body.Sender` against an authorized allowlist (e.g., known subscriber/workflow-owner addresses configured for the DON or the specific workflow/trigger being invoked), and apply per-sender rate limiting, before saving the callback and calling `don.SendToNode`. This should replace/implement the `// TODO: apply allowlist and rate-limiting here` marker at `core/services/gateway/handlers/capabilities/handler.go:384`.

### Proof of Concept
Go handler-level integration test plan (extending `core/services/gateway/handlers/capabilities/handler_test.go`):
1. Set up a `handler` with a `donConfig.Members` list of known node addresses (as existing tests do), and a mock `handlers.DON` (`don.SendToNode`) using `mockery`-generated mock.
2. Generate a brand-new ECDSA key (`crypto.GenerateKey()`) that is NOT present in `donConfig.Members` and has no relationship to any configured subscriber/allowlist.
3. Build a valid `api.Message` with `Body.Method = MethodWebAPITrigger`, a valid `TriggerRequestPayload` with a fresh `Timestamp`, and sign it with the new unauthorized key (mirroring `triggerRequest` helper already used in `handler_test.go`).
4. Call `handler.HandleLegacyUserMessage(ctx, msg, callback)`.
5. Assert: (a) the returned error is `nil`; (b) `don.SendToNode` (mock) was called once for each DON member address in `donConfig.Members`, proving the message was routed/broadcast despite the sender not being in any allowlist; (c) `callback.Wait` resolves successfully rather than being rejected with an authorization/allowlist error — proving no authorization gate exists on the legacy trigger path.

### Citations

**File:** core/services/gateway/api/message.go (L54-87)
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
```

**File:** core/services/gateway/gateway.go (L264-269)
```go
	startTime := time.Now()
	var method string
	callback := handlerscommon.NewCallback()
	if isLegacyRequest {
		method = msg.Body.Method
		err = h.HandleLegacyUserMessage(ctx, msg, callback)
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-384)
```go
	// TODO: apply allowlist and rate-limiting here
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

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L365-365)
```go
	// TODO: Validate Senders and rate limit check, pending question in trigger about where senders and rate limits are validated
```
