### Title
`OutgoingConnectorHandler.HandleGatewayMessage` dispatches gateway responses into the pending-request channel without verifying `msg.Body.Sender` against an authorized DON/gateway allowlist - ([File: core/capabilities/webapi/outgoing_connector_handler.go])

### Summary
`HandleGatewayMessage` (`core/capabilities/webapi/outgoing_connector_handler.go:303-371`) only validates the JSON-RPC/message envelope shape via `hc.ValidatedMessageFromReq` and applies per-sender/global rate limiting, then delivers the message straight into the pending-response channel `ch` keyed solely by `body.MessageId`. There is no check that `body.Sender` (the address recovered from the message signature) is a member of an expected/allowlisted DON or gateway node set before the payload is handed off to the waiting caller of `HandleSingleNodeRequest`.

### Finding Description
The relevant code path is: [1](#0-0) 
`msg, err := hc.ValidatedMessageFromReq(req)` only checks JSON-RPC envelope correctness and calls `api.Message.Validate()` (`core/services/gateway/handlers/common/message_util.go:36-58`), which is limited to structural/signature-format validation — it does not check the signer's address against any DON/gateway allowlist maintained by this handler. After that, `c.responses.get(body.MessageId)` looks up the pending channel purely by the caller-generated `messageID`, and `senderAllow, globalAllow := c.incomingRateLimiter.AllowVerbose(body.Sender)` (line 318) only throttles by sender identity, it does not authorize it. If the messageID matches an outstanding request, the message is pushed directly into `ch` (line 362), which unblocks `handleSingleNodeRequest`'s `select` (`outgoing_connector_handler.go:192-206`) and returns the attacker-supplied payload as the trusted capability response.

No code in this function cross-references `body.Sender` against the DON member list, the originating gateway's configured node address, or any allowlist structure. The only identity-adjacent check is rate limiting, which is a quota mechanism, not an authorization mechanism.

### Impact Explanation
This is a request/response impersonation and cross-user response confusion issue: an attacker who can get a validly-signed `api.Message` with a live `messageID` routed into this handler can inject a fabricated `capabilities.Response` payload (e.g. for `MethodWebAPITarget`, `MethodComputeAction`, or `MethodWorkflowSyncer`) that is then trusted and returned by `HandleSingleNodeRequest` as the legitimate capability/gateway response, potentially influencing workflow execution or downstream job results with attacker-controlled data. This maps to the Chainlink bounty impact class of "response/data injection leading to unauthorized action based on falsified off-chain data."

### Likelihood Explanation
Exploitability depends on an attacker's ability to get a message with a forged signature and a currently-outstanding `messageID` delivered through the node's already-authenticated Gateway websocket connection into `readLoop`/`HandleGatewayMessage` (`core/services/gateway/connector/connector.go:268-298`). The websocket transport itself is only established to pre-configured, operator-defined Gateway endpoints via a challenge-response handshake (`NewAuthHeader`/`ChallengeResponse`), so this finding's actual severity hinges on whether the Gateway-side relay (outside this file) itself enforces sender-to-DON allowlisting before forwarding messages to the node. I could not fully verify `api.Message.Validate()` internals (`core/services/gateway/api/message.go`) or the Gateway-side forwarding/allowlist logic within the available tool budget, so I cannot conclusively confirm that an attacker outside the trusted Gateway boundary can reach this function with an arbitrary signer. This is a real gap in defense-in-depth at this specific function regardless, since it performs no independent Sender authorization even though it is the last line of defense before an untrusted-signature payload is trusted as a genuine response.

### Recommendation
In `HandleGatewayMessage`, before dispatching into `ch`, validate that `body.Sender` matches the expected/allowlisted address(es) for the DON/gateway associated with `body.DonId`/`gatewayID` (e.g., cross-check against the node's configured DON member set or the specific gateway's known signing address), rejecting and logging any message whose sender is not authorized, independent of any upstream Gateway-side filtering.

### Proof of Concept
1. Unit test in `core/capabilities/webapi/outgoing_connector_handler_test.go`: start `handleSingleNodeRequest` in a goroutine to create a pending channel for a known `messageID`.
2. Construct an `api.Message` with that `messageID`, a valid JSON-RPC envelope, and a signature produced by a non-DON-member EOA key (not the configured gateway/DON signer).
3. Call `HandleGatewayMessage(ctx, gatewayID, req)` directly with this forged message.
4. Assert that today the message is delivered to `ch` and returned by `handleSingleNodeRequest` (demonstrating the gap), and that after the recommended fix the handler instead logs/drops the message and the original call times out or errors, never returning the attacker payload.

### Citations

**File:** core/capabilities/webapi/outgoing_connector_handler.go (L303-317)
```go
func (c *OutgoingConnectorHandler) HandleGatewayMessage(ctx context.Context, gatewayID string, req *jsonrpc.Request[json.RawMessage]) error {
	msg, err := hc.ValidatedMessageFromReq(req)
	if err != nil {
		c.lggr.Errorw("failed to validate request", "err", err, "gatewayID", gatewayID)
		return nil
	}
	body := &msg.Body
	l := logger.With(c.lggr, "gatewayID", gatewayID, "method", body.Method, "messageID", msg.Body.MessageId)

	ch, ok := c.responses.get(body.MessageId)
	if !ok {
		l.Warnw("no response channel found; this may indicate that the node timed out the request")
		return nil
	}

```
