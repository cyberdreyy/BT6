## Analysis

The bug class from the report (missing access-restriction modifier allowing unauthorized/fraudulent registration and code generation) maps most directly to the Chainlink gateway's `HandleLegacyUserMessage` path in the web-api capabilities handler, where an explicit `TODO` marks a missing allowlist check before dispatching a user-triggered workflow request.

### Title
Missing allowlist/authorization check before dispatching `web_api_trigger` requests to DON nodes - (File: `core/services/gateway/handlers/capabilities/handler.go`)

### Summary
`HandleLegacyUserMessage` in the gateway's WebAPI capabilities handler processes incoming user messages and forwards `web_api_trigger` requests to every member node of the DON, but it never verifies that the caller (workflow owner) is authorized/allowlisted to trigger the target workflow before doing so.

### Finding Description
`HandleLegacyUserMessage` validates payload structure, timestamp freshness, and that `msg.Body.Method == MethodWebAPITrigger`, then immediately converts the message to a request and fans it out to every DON member via `don.SendToNode`. There is no authorization or allowlist check on the sender before this dispatch — the code contains an explicit acknowledgment of the gap: [1](#0-0) 
This differs from the sibling vault gateway handler, which enforces `AuthorizeRequest`/allowlist checks (`h.requestProcessor.ProcessRequest`) before dispatching any authorized action: [2](#0-1) 
The `Handler` interface itself only requires message structural validity (`HandleLegacyUserMessage`), and this specific implementation skips the authorization step that the report's `onlyDiamond()`-equivalent restriction would provide. [3](#0-2) 

### Impact Explanation
Because no allowlist/ownership check gates the request before fan-out, any unprivileged client able to reach the gateway's user-message endpoint with a validly-signed (but not necessarily authorized-for-this-workflow) message can cause `web_api_trigger` requests to be broadcast to all DON nodes for a given `donId`/receiver, potentially triggering workflow runs the caller does not own or is not entitled to invoke — directly analogous to "unauthorized registration" in the reported bug (unauthorized job/workflow run).

### Likelihood Explanation
Reachable directly from any external client sending a message through the standard gateway user-message flow; the check is missing entirely (not merely misconfigured), and the `TODO` in the code confirms it was never implemented, making exploitation deterministic once a syntactically valid signed message with method `web_api_trigger` and correct DON/timestamp fields is sent.

### Recommendation
Add an allowlist/authorization check (equivalent to `AuthorizeRequest` used in `core/services/gateway/handlers/vault/handler.go`) inside `HandleLegacyUserMessage` before forwarding requests to DON members, verifying the sender is permitted to trigger the specified workflow/receiver.

### Proof of Concept
1. Craft a `Message` with `Body.Method = "web_api_trigger"`, a valid `Body.Timestamp`, and a signature from any key (per `Message.Sign`/`Validate` in `core/services/gateway/api/message.go`).
2. Submit it to the gateway's user endpoint for a target `DonId`/`Receiver` the caller does not own.
3. Observe that `HandleLegacyUserMessage` skips straight to `common.ValidatedRequestFromMessage` and `don.SendToNode` for all DON members with no ownership/allowlist verification, as shown at lines 384-420 of `core/services/gateway/handlers/capabilities/handler.go`.

### Citations

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

**File:** core/services/gateway/handlers/vault/handler.go (L430-444)
```go

	if !vaulttypes.IsGatewaySecretsMethod(req.Method) {
		return h.sendImmediateUserResponse(ctx, req, callback, api.UnsupportedMethodError, errors.New("this method is unsupported: "+req.Method))
	}

	_, cachedPublicKey := h.getCachedPublicKey()
	authorized, err := h.requestProcessor.ProcessRequest(ctx, &req, cachedPublicKey)
	if err != nil {
		if vaultcap.IsInvalidVaultParamsError(err) {
			return h.sendImmediateUserResponse(ctx, req, callback, api.InvalidParamsError, err)
		}
		h.lggr.Errorw("request not authorized", "method", req.Method, "requestID", req.ID, "hasAuth", req.Auth != "", "error", err)
		return errors.New("request not authorized: " + err.Error())
	}
	authorizedOwner := authorized.AuthResult.AuthorizedOwner()
```

**File:** core/services/gateway/handlers/handler.go (L31-47)
```go
type Handler interface {
	job.ServiceCtx

	// Each user request is processed by a separate goroutine, which:
	//   1. calls HandleUserMessage
	//   2. waits on callbackCh with a timeout
	HandleLegacyUserMessage(ctx context.Context, msg *api.Message, callback Callback) error

	// Each user request is processed by a separate goroutine, which:
	//   1. calls HandleUserMessage
	//   2. waits on callbackCh with a timeout
	HandleJSONRPCUserMessage(ctx context.Context, jsonRequest jsonrpc.Request[json.RawMessage], callback Callback) error

	// Handlers should not make any assumptions about goroutines calling HandleNodeMessage.
	// should be non-blocking
	// should validate the message inside the response
	HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error
```
