### Title
Gateway routes signed requests to any DON purely on self-declared `Body.DonId` with no sender-membership binding, allowing a legitimate signer of one DON to reach another DON's handler as an apparently-authenticated sender - (File: core/services/gateway/gateway.go)

### Summary
`gateway.ProcessRequest` selects the target DON handler using the attacker-controlled `msg.Body.DonId` field and only calls `msg.Validate()`, which verifies the signature is self-consistent via `ExtractSigner` but never checks that the recovered signer is a member/allowlisted node of that `DonId`. At least one handler, the web-API capabilities handler, has no sender/allowlist check either (explicit `TODO: apply allowlist and rate-limiting here`), so a validly-signed message with a spoofed `DonId` is forwarded to that DON's nodes.

### Finding Description
In `gateway.ProcessRequest` (`core/services/gateway/gateway.go:250-262`), for legacy requests the code does:
```go
isLegacyRequest = true
if err = msg.Validate(); err != nil { ... }
handlerKey = msg.Body.DonId
h, ok = g.handlers[handlerKey]
``` [1](#0-0) 

`Message.Validate()` (`core/services/gateway/api/message.go:54-88`) checks field lengths and calls `ExtractSigner()`, then blindly assigns the recovered address to `m.Body.Sender` — it never checks whether that recovered signer belongs to `m.Body.DonId`'s node/member set: [2](#0-1) 

`ExtractSigner` (`core/services/gateway/api/message.go:124-134`) only recovers the address that produced the signature over `MessageId||Method||DonId||Receiver||Payload`; it proves key ownership but not DON membership, since `DonId` is signed data supplied by the attacker itself, not a value the gateway independently verifies: [3](#0-2) 

The routed-to handler for DON Y (e.g. the web-API capabilities handler) then processes the message without any additional sender/allowlist check. `handler.HandleLegacyUserMessage` in `core/services/gateway/handlers/capabilities/handler.go:341-421` validates payload structure/timestamp/method but has an explicit unresolved TODO for allowlisting:
```go
// TODO: apply allowlist and rate-limiting here
if msg.Body.Method != MethodWebAPITrigger { ... }
...
for _, member := range h.donConfig.Members {
    err = errors.Join(err, don.SendToNode(ctx, member.Address, req))
}
``` [4](#0-3) 

The corresponding test file confirms this gap is known and unaddressed: `// TODO: Validate Senders and rate limit check, pending question in trigger about where senders and rate limits are validated`. [5](#0-4) 

Exploit flow: attacker holds any ECDSA keypair (not registered anywhere in DON Y's node list). They build a `Message` with `Body.DonId = "Y"`, sign it with their own key (`Sign`/`SignKS`), and POST it to the gateway's `/user` endpoint. `ProcessRequest` decodes it, calls `msg.Validate()` which succeeds (signature is internally consistent), sets `handlerKey = "Y"`, looks up `g.handlers["Y"]`, and forwards the message into DON Y's `HandleLegacyUserMessage`, which broadcasts the request to all of DON Y's node members with no check that the recovered `Body.Sender` is a legitimate DON-Y client/allowlisted address.

### Impact Explanation
This allows request impersonation / authorization bypass against DON-scoped services: an entity with no relationship to DON Y (not a member, not on any allowlist) can have arbitrary web-API-trigger requests dispatched to DON Y's oracle nodes as if from a legitimately signed sender, since the handler performs no sender-to-DON binding check before fanning the message out to `donConfig.Members`. Depending on downstream node-side processing (e.g., trigger event injection into DON Y workflows), this maps to Chainlink's "unauthorized job/workflow trigger" or "authentication/authorization bypass" bounty impact class. The impact is bounded by what the DON-Y nodes themselves do with an unauthorized web_api_trigger message (their own node-side validation would be the last line of defense), but the gateway-side authorization control described in the code's own TODOs is absent.

### Likelihood Explanation
Preconditions are minimal: attacker only needs any ECDSA keypair (self-generated, free) and knowledge of a valid/target `DonId` string (these are often discoverable, e.g., from configuration, prior legitimate messages, or documentation). No privileged credentials, no operator access, no node compromise required — this is fully within the "unprivileged attacker sending signed gateway requests" threat model. The path is directly reachable via the public `/user` HTTP route into `ProcessRequest` and is deterministically reproducible.

### Recommendation
Add sender-authorization binding at either the gateway or handler layer: after `msg.Validate()` recovers `Body.Sender`, check that the recovered address is present in the target DON's configured allowlist/member/user set for the relevant method before dispatching to `HandleLegacyUserMessage`/`HandleNodeMessage`. Specifically, implement the outstanding TODO in `core/services/gateway/handlers/capabilities/handler.go` (`HandleLegacyUserMessage`) to verify `msg.Body.Sender` against a per-DON allowlist prior to fanning the request out to `don.SendToNode` for all `h.donConfig.Members`.

### Proof of Concept
Go handler-level test plan in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Set up `handler` for `donID = "workflow_don_1"` with `donConfig.Members` populated with known node addresses, mocking `don.SendToNode`.
2. Generate a fresh, unrelated ECDSA keypair not present in any allowlist/config (`crypto.GenerateKey()`), distinct from the keys used to configure the DON.
3. Build a `triggerRequest`-style `api.Message` with `Body.DonId = "workflow_don_1"` and `Body.Method = MethodWebAPITrigger`, sign it with the unrelated keypair via `msg.Sign(unrelatedKey)`, and call `msg.Validate()` — assert it succeeds and `msg.Body.Sender` equals the unrelated key's address.
4. Call `handler.HandleLegacyUserMessage(ctx, msg, cb)` and assert (current behavior) that `don.SendToNode` is invoked for all `donConfig.Members`, i.e., the message is forwarded despite the signer not being a recognized/allowlisted sender for this DON — demonstrating the missing binding.
5. After the fix, add assertion that `HandleLegacyUserMessage` returns an authorization error and `don.SendToNode` is never called when `msg.Body.Sender` is not in the DON's allowlist.

### Citations

**File:** core/services/gateway/gateway.go (L250-262)
```go
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
```

**File:** core/services/gateway/api/message.go (L82-88)
```go
	signerBytes, err := m.ExtractSigner()
	if err != nil {
		return err
	}
	m.Body.Sender = utils.StringToHex(string(signerBytes))
	return nil
}
```

**File:** core/services/gateway/api/message.go (L124-134)
```go
func (m *Message) ExtractSigner() (signerAddress []byte, err error) {
	if m == nil {
		return nil, errors.New("nil message")
	}
	rawData := GetRawMessageBody(&m.Body)
	signatureBytes, err := hex.DecodeString(m.Signature)
	if err != nil {
		return nil, err
	}
	return gw_common.ExtractSigner(signatureBytes, rawData...)
}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-419)
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
```

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L365-366)
```go
	// TODO: Validate Senders and rate limit check, pending question in trigger about where senders and rate limits are validated
}
```
