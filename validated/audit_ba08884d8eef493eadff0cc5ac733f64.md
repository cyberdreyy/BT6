Confirmed: `ExtractSigner` recovers the signer address from any valid ECDSA signature over the message body — it does not check the recovered address against any allowlist. `Message.Validate()` (in `core/services/gateway/api/message.go:54-88`) likewise only validates lengths/format and calls `ExtractSigner`, setting `m.Body.Sender` to whatever address the signature recovers to, again with no allowlist check.

### Title
Legacy gateway web API trigger path forwards any self-signed user request to all DON nodes without sender allowlisting - (File: core/services/gateway/handlers/capabilities/handler.go)

### Summary
The gateway's legacy user-message path (`HandleLegacyUserMessage`) accepts any externally submitted `web_api_trigger` message as long as it has a syntactically valid, self-consistent signature and a fresh timestamp, then broadcasts it unmodified to every member of the target DON. There is no allowlist check on the sender before forwarding, despite an explicit TODO in the code acknowledging this gap.

### Finding Description
`gateway.ProcessRequest` (`core/services/gateway/gateway.go:218-292`) routes any incoming legacy (DonId-bearing) request to `h.HandleLegacyUserMessage` after only calling `msg.Validate()`, which checks field lengths and null-byte suffixes and calls `ExtractSigner()` [1](#0-0) . `ExtractSigner` recovers whatever address signed the message — it is not checked against any known/allowlisted key [2](#0-1) . Any caller can locally generate an ECDSA keypair, sign a `web_api_trigger` message body, and produce a message that passes `Validate()`.

In `HandleLegacyUserMessage`, after decoding the payload and checking only `payload.Timestamp` freshness and that `msg.Body.Method == MethodWebAPITrigger`, the code contains an explicit unaddressed gap:
```
// TODO: apply allowlist and rate-limiting here
if msg.Body.Method != MethodWebAPITrigger {
``` [3](#0-2) 

No sender-allowlist check or per-sender rate limiting is applied before the request is saved as a callback and broadcast: `for _, member := range h.donConfig.Members { don.SendToNode(...) }` [4](#0-3) . This mirrors the reported bug class: once a shallow, generic check passes (here: signature well-formed + method name matches), the system trusts the rest of the payload/sender implicitly and grants full downstream execution (delivery to every DON node) without any allowlist gate — the same "pass one narrow check, then anything goes" pattern as the 0x `transformERC20` selector check.

By contrast, the node-side trigger handler (`core/capabilities/webapi/trigger/trigger.go`) does perform a sender allowlist check ("unauthorized Sender ...") [5](#0-4) , and the newer v2 handler pipeline enforces per-node/global rate limiting [6](#0-5) , confirming that allowlisting/rate-limiting is the intended, but here (legacy path) missing, control.

### Impact Explanation
An unauthenticated/unprivileged client (anyone able to reach the gateway's HTTP endpoint) can craft self-signed trigger messages and have the gateway broadcast them to every node of a target DON, consuming node/gateway resources and reaching capability logic that was intended to be reachable only by allowlisted senders. Whether this is fully exploitable end-to-end depends on downstream allowlist checks in the specific capability being triggered (e.g., trigger.go does check sender allowlists on the node side for some paths), so actual impact is bounded by whichever downstream consumer processes `MethodWebAPITrigger` messages from this legacy path — but the gateway itself performs no such check, which is a real, code-confirmed gap (not a hypothetical extrapolation) marked by the maintainers' own TODO.

### Likelihood Explanation
High likelihood of reachability: this is the exact legacy path exercised by `gateway.ProcessRequest` for any DonId-bearing request and is directly reachable from network-facing input with no authentication beyond a self-generated signature. The missing check is explicitly flagged in the source as a known incomplete TODO, indicating it was never wired in for this specific code path, unlike sibling gateway handlers.

### Recommendation
Before broadcasting to DON members in `HandleLegacyUserMessage`, validate `msg.Body.Sender` against the DON's configured/known allowlist (as is done for other gateway message paths, e.g. the vault gateway handler's `AllowListBasedAuth`/`AuthorizeRequest` pattern [7](#0-6) ) and add per-sender rate limiting, removing the outstanding TODO.

### Proof of Concept
1. Generate an arbitrary ECDSA keypair (no relation to any registered/allowlisted workflow owner).
2. Construct a `Message` with `Body.Method = "web_api_trigger"`, a valid `TriggerRequestPayload` with a fresh `Timestamp`, and sign it with the arbitrary key via `Message.Sign` [8](#0-7) .
3. Submit the raw JSON-RPC request to the gateway's public HTTP endpoint so it is routed via `gateway.ProcessRequest` → `msg.Validate()` (passes, since only format is checked) → `HandleLegacyUserMessage`.
4. Observe that `HandleLegacyUserMessage` broadcasts the message to every DON member in `h.donConfig.Members` [9](#0-8)  without ever checking whether the recovered signer address is permitted to trigger that DON — confirming the missing allowlist gate.

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

**File:** core/services/gateway/api/message.go (L96-108)
```go
func (m *Message) Sign(privateKey *ecdsa.PrivateKey) error {
	if m == nil {
		return errors.New("nil message")
	}
	rawData := GetRawMessageBody(&m.Body)
	signature, err := gw_common.SignData(privateKey, rawData...)
	if err != nil {
		return err
	}
	m.Signature = utils.StringToHex(string(signature))
	m.Body.Sender = strings.ToLower(crypto.PubkeyToAddress(privateKey.PublicKey).Hex())
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-396)
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

**File:** core/capabilities/webapi/trigger/trigger_test.go (L276-289)
```go
	t.Run("sad case Not Allowed Sender", func(t *testing.T) {
		gatewayRequest := gatewayRequest(t, privateKey2, []string{"ad_hoc_price_update"}, "")
		th.connector.EXPECT().SignMessage(mock.Anything, mock.Anything).Return([]byte("signature"), nil).Once()
		th.connector.On("SendToGateway", mock.Anything, mock.Anything, mock.Anything).Run(func(args mock.Arguments) {
			resp, err2 := getResponseFromArg(args.Get(2))
			require.NoError(t, err2)

			require.Equal(t, ghcapabilities.TriggerResponsePayload{Status: "ERROR", ErrorMessage: "unauthorized Sender 0x2dAC9f74Ee66e2D55ea1B8BE284caFedE048dB3A, messageID 12345"}, resp)
		}).Return(nil).Once()

		th.trigger.HandleGatewayMessage(ctx, "gateway1", gatewayRequest)
		requireNoChanMsg(t, channel)
		requireNoChanMsg(t, channel2)
	})
```

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L238-254)
```go
func (h *gatewayHandler) HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	if resp.ID == "" {
		return fmt.Errorf("received response with empty request ID from node %s", nodeAddr)
	}
	h.lggr.Debugw("handling incoming node message", "requestID", resp.ID, "nodeAddr", nodeAddr)
	nodeRateLimiter, ok := h.perNodeRateLimiters[nodeAddr]
	if !ok {
		return fmt.Errorf("received message from unexpected node %s", nodeAddr)
	}
	if !nodeRateLimiter.Allow(ctx) {
		h.metrics.IncrementCapabilityNodeThrottled(ctx, nodeAddr, h.lggr)
		return fmt.Errorf("rate limit exceeded for node %s", nodeAddr)
	}
	if !h.globalNodeRateLimiter.Allow(ctx) {
		h.metrics.IncrementGlobalThrottled(ctx, h.lggr)
		return errors.New("global rate limit exceeded")
	}
```

**File:** core/capabilities/vault/gw_handler.go (L108-111)
```go
	if authorizer == nil {
		allowListBasedAuth := NewAllowListBasedAuth(lggr, workflowRegistrySyncer)
		authorizer = NewAuthorizer(allowListBasedAuth, jwtBasedAuth, lggr)
	}
```
