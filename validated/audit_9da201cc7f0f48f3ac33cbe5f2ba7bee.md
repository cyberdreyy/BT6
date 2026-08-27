### Title
Missing upper-bound (future) validation on user-supplied `Timestamp` allows indefinite replay of Web API trigger messages - (File: `core/services/gateway/handlers/capabilities/handler.go`)

### Summary
`handler.HandleLegacyUserMessage` is the internet-facing entry point that processes `web_api_trigger` requests forwarded by the Gateway from unauthenticated external callers before dispatching them to DON nodes. Its only anti-replay/freshness control is a check on the caller-supplied `Timestamp` field, but that check validates only a lower bound ("not too old") and never validates an upper bound ("not too far in the future" / fits a sane range). This mirrors the reported bug class: a time-like field accepted from an untrusted caller with no upper-bound check, which can be abused to defeat a freshness/waiting-period control.

### Finding Description
In `HandleLegacyUserMessage`, the payload's `Timestamp` is only checked for the "too old" condition: [1](#0-0) 

There is no corresponding check that `payload.Timestamp` is not unreasonably far in the future, and no bound tying it to `time.Now()` plus some max clock-skew allowance. Because the message signature (`msg.Validate()` in `ValidatedMessageFromReq`, called by the caller of this handler) authenticates only the sender's key and payload bytes — not a server-issued nonce or expiry — the `Timestamp` field is the sole mechanism intended to bound how long a given signed message remains acceptable: [2](#0-1) [3](#0-2) 

Since `payload.Timestamp` is attacker-controlled and only lower-bounded, an unprivileged caller can craft (and sign, using their own key) a `web_api_trigger` request with an arbitrarily large `Timestamp` (e.g., far in the future). The staleness check `uint(time.Now().Unix())-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp)` will never evaluate true for such a message, regardless of how much wall-clock time has actually passed — the message is treated as "fresh" forever. This is directly analogous to the `mustStartAtOrAfter` bug: an unbounded, user-controlled time-like value is accepted without an upper-bound/sanity check, defeating the intended time-window enforcement.

Once past this check, the message is dispatched unconditionally to every DON member for processing: [4](#0-3) 

### Impact Explanation
A captured, validly-signed `web_api_trigger` message (e.g. intercepted from logs, a compromised intermediary, or simply retained by the original signer) can be replayed to the Gateway at any point in the future without being rejected as stale, because the freshness window has no upper bound to anchor it to real elapsed time. This allows repeated/unauthorized re-triggering of a workflow execution (`don.SendToNode` fan-out to all DON members) outside of the intended freshness window that the `MaxAllowedMessageAgeSec` control is meant to enforce — an unauthorized job run / replay-protection bypass reachable directly from an unauthenticated HTTP client hitting the Gateway.

### Likelihood Explanation
Reachable by any external, unauthenticated client capable of submitting a signed `web_api_trigger` request through the Gateway's legacy message path (`ProcessRequest` → `HandleLegacyUserMessage`). No privileged access or node compromise is required — only the ability to sign a message with an arbitrary key (any external Ethereum key satisfies `allowedSenders` requirements enforced downstream) and to replay/resend a previously captured request.

### Recommendation
Add an explicit upper-bound check on `payload.Timestamp`, rejecting messages whose timestamp is beyond `time.Now()` plus a small allowed clock-skew, in addition to the existing "too old" check, e.g.:
```go
now := uint(time.Now().Unix())
if payload.Timestamp > int64(now+allowedClockSkewSec) || now-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp) {
    // reject as stale/invalid
}
```
Consider additionally bounding/validating the type range (reject negative or absurdly large values before the `uint()` cast) to avoid conversion surprises, consistent with the recommended `uint56`-style bound check in the referenced report.

### Proof of Concept
1. An external client crafts a `web_api_trigger` `api.Message` with `Body.Payload` containing `TriggerRequestPayload{Timestamp: <far future value>, ...}` and signs it with its own key.
2. The client submits it to the Gateway's `ProcessRequest` endpoint; `HandleLegacyUserMessage` reaches the staleness check at `core/services/gateway/handlers/capabilities/handler.go:372`.
3. `uint(time.Now().Unix()) - MaxAllowedMessageAgeSec > uint(payload.Timestamp)` evaluates false indefinitely (since `payload.Timestamp` is far greater than any realistic `time.Now().Unix()`), so the message is never flagged as stale.
4. The client (or anyone who captures this exact signed message) can resend it at any later time; it is still forwarded to all DON members via `don.SendToNode`, re-triggering the workflow outside the intended freshness window.

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L372-383)
```go
	if uint(time.Now().Unix())-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp) {
		h.lggr.Errorw("stale message")
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.HandlerError),
				"stale message",
				nil,
			),
			ErrorCode: api.HandlerError,
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

**File:** core/services/gateway/handlers/common/message_util.go (L36-57)
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
```

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
