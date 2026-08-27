### Title
Internal node address disclosure via unfiltered error propagation from `HandleLegacyUserMessage` to caller - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` accumulates raw `don.SendToNode` errors via `errors.Join` and returns them to its caller unmodified. The gateway's `ProcessRequest` in `core/services/gateway/gateway.go` then embeds that raw error string directly into the JSON-RPC error response sent back to the (possibly unauthenticated) HTTP caller, which can include internal node addresses such as `"node %s not found"`.

### Finding Description
In `core/services/gateway/handlers/capabilities/handler.go`, the loop: [1](#0-0) 
joins per-member `don.SendToNode` errors and returns the aggregated error as the function's return value, bypassing the `callback.SendResponse` path used for all other error branches in this function (which use generic, sanitized error messages via `codec.EncodeNewErrorResponse`).

The concrete implementation of `SendToNode` in `core/services/gateway/connectionmanager.go` can produce an error that embeds the raw node address: [2](#0-1) 
Specifically `fmt.Errorf("node %s not found", nodeAddress)` and `fmt.Errorf("error encoding request for node %s: %w", nodeAddress, err)` directly interpolate the internal DON member address into the error text.

The caller of `HandleLegacyUserMessage`, `gateway.ProcessRequest`, does not sanitize this error before it reaches the wire: [3](#0-2) 
`err.Error()` is passed directly as the `Message` field of the JSON-RPC error response returned in the HTTP body to the caller, via `newError`: [4](#0-3) 

Unlike every other error branch inside `HandleLegacyUserMessage` (payload decode errors, stale message, unsupported method, transform errors), which route through `callback.SendResponse` with a controlled, generic message and a specific `api.ErrorCode`, the per-node send-error path skips that sanitized response mechanism entirely and lets the raw internal error escape through the function's return value straight to the HTTP layer.

I was unable to fully verify from the index whether `nodeState.conn.Write` (in `core/services/gateway/network/wsconnection.go`) itself embeds the node address in its error text for the "node is offline/disconnected" case (the file's `Write` implementation was not retrieved before the tool budget ran out), so the specific wording of the "offline node" error is uncertain. However, the "node %s not found" and "error encoding request for node %s" errors in `SendToNode` are confirmed to leak the raw address string, and both are reachable through the exact same `errors.Join` accumulation path in `HandleLegacyUserMessage`.

### Impact Explanation
This is an information-disclosure issue: an unauthenticated/unprivileged HTTP caller submitting a legacy trigger message can, under certain conditions (e.g., DON member address not present in the gateway's configured node map, or an encoding failure), receive a JSON-RPC error response containing internal DON member address strings that should not be exposed to external callers of the gateway's public HTTP endpoint. This matches a low/informational-severity "internal topology/information disclosure" impact class rather than a direct compromise (no keys, funds, or authentication bypass are involved).

### Likelihood Explanation
Requires only an unauthenticated caller submitting a legacy trigger message to the gateway (`msg.Body.DonId` set, hits the `isLegacyRequest` path in `ProcessRequest`), and a DON member that triggers a `SendToNode` failure of the address-embedding kind (misconfigured/mismatched member address, or an encoding failure). It does not require any privileged role. It's a straightforward, repeatable condition tied to node connectivity state, but the specific address-in-error-message case (`"node not found"`) is more of an edge/misconfiguration condition than "node simply offline," which somewhat limits everyday exploitability compared to the audit prompt's premise.

### Recommendation
In `HandleLegacyUserMessage`, do not return raw `don.SendToNode` errors (or any `errors.Join` result containing them) directly to the caller. Instead, log the detailed error internally (as already done via `h.lggr`), and send a generic, sanitized error via `callback.SendResponse`/`codec.EncodeNewErrorResponse` with an appropriate `api.ErrorCode` (e.g., a new `NodeSendError` code), consistent with the other branches in this function. Also audit `SendToNode` in `core/services/gateway/connectionmanager.go` to avoid interpolating node addresses into error strings that might propagate upward, or wrap/redact such errors before they leave the connection-manager boundary.

### Proof of Concept
1. Unit test `HandleLegacyUserMessage` in `core/services/gateway/handlers/capabilities/handler_test.go` with a mock `handlers.DON` (`mocks.DON`) whose `SendToNode` returns `fmt.Errorf("node %s not found", "0xabc123...")` for one member.
2. Call `h.HandleLegacyUserMessage(ctx, validTriggerMsg, callback)` and assert the returned `error` is non-nil and `err.Error()` contains the raw address string `"0xabc123..."` — demonstrating the leak at the handler boundary.
3. Handler-level integration test on `gateway.ProcessRequest`: wire up a `gateway` with this handler/mock DON, submit a legacy trigger HTTP-equivalent request, and assert that the JSON-RPC error `Message` field returned by `ProcessRequest` (via `newError`) contains the internal node address substring — confirming it reaches the unprivileged caller's HTTP response unmodified.
4. Expected fix state: after remediation, the same test should show a generic message (e.g., "internal error sending to DON members") with no address substring, while the address still appears in the internal log line (`h.lggr.Errorw`).

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L416-420)
```go
	// Send original request to all nodes
	for _, member := range h.donConfig.Members {
		err = errors.Join(err, don.SendToNode(ctx, member.Address, req))
	}
	return err
```

**File:** core/services/gateway/connectionmanager.go (L326-339)
```go
func (m *donConnectionManager) SendToNode(ctx context.Context, nodeAddress string, req *jsonrpc.Request[json.RawMessage]) error {
	if req == nil {
		return errors.New("nil request")
	}
	data, err := jsonrpc.EncodeRequest(req)
	if err != nil {
		return fmt.Errorf("error encoding request for node %s: %w", nodeAddress, err)
	}
	nodeState := m.nodes[nodeAddress]
	if nodeState == nil {
		return fmt.Errorf("node %s not found", nodeAddress)
	}
	return nodeState.conn.Write(ctx, websocket.BinaryMessage, data)
}
```

**File:** core/services/gateway/gateway.go (L267-276)
```go
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

**File:** core/services/gateway/gateway.go (L294-310)
```go
func newError(id string, errCode api.ErrorCode, errMsg string) ([]byte, int) {
	response := jsonrpc2.Response[json.RawMessage]{
		Version: jsonrpc2.JsonRpcVersion,
		ID:      id,
		Error: &jsonrpc2.WireError{
			Code:    api.ToJSONRPCErrorCode(errCode),
			Message: errMsg,
			Data:    nil,
		},
	}
	rawResponse, err := json.Marshal(response)
	if err != nil {
		rawResponse = []byte("fatal error" + err.Error())
	}
	promRequest.WithLabelValues(errCode.String()).Inc()
	return rawResponse, api.ToHttpErrorCode(errCode)
}
```
