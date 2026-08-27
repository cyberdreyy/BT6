### Title
Confidential-relay gateway handler dispatches JSON-RPC user requests to the entire DON without verifying `req.Auth` - ([File: core/services/gateway/handlers/confidentialrelay/handler.go])

### Summary
The `Handler` interface's `HandleJSONRPCUserMessage` contract does not mandate an auth/signature check, and each concrete handler is responsible for verifying `req.Auth` itself. The `vault` handler does this via `h.requestProcessor.ProcessRequest(ctx, &req, cachedPublicKey)`, but the `confidentialrelay` handler's `HandleJSONRPCUserMessage` performs no such check at all before fanning the raw request out to every DON member node.

### Finding Description
`Handler.HandleJSONRPCUserMessage` in `core/services/gateway/handlers/handler.go` takes only `ctx`, the raw `jsonrpc.Request[json.RawMessage]`, and a `Callback` — there is no interface-enforced authentication step. [1](#0-0) 

The vault handler explicitly authenticates/authorizes every non-public-key request before doing any work, deriving an `authorizedOwner` from `req.Auth`-derived authorization data via `h.requestProcessor.ProcessRequest`: [2](#0-1) 

In contrast, `confidentialrelay`'s implementation performs only structural validation (non-empty, bounded-length request ID) and then immediately creates an active request and fans it out to every node in the DON — there is no call to any authorizer, no reference to `req.Auth`, and no signature check on the inbound user request: [3](#0-2) [4](#0-3) 

The gateway's HTTP entrypoint (`gateway.ProcessRequest`) decodes the raw request/auth pair and dispatches directly to whichever handler owns the service, without itself enforcing any authentication — that responsibility is left entirely to the handler: [5](#0-4) 

The code comment on `forwardBundle` confirms the design intent that the gateway "makes no trust decision" for confidential-relay traffic and defers all signature/quorum verification to the enclave on the *response* path: [6](#0-5) 

However, this defers verification only for the *responses* aggregated from nodes — it does nothing to authenticate the *inbound* `MethodSecretsGet`/`MethodCapabilityExec` request before it is forwarded to every DON member. Any unauthenticated caller can submit a JSON-RPC request to the confidential-relay service (with empty or forged `Auth`) and have the gateway broadcast it, with attacker-controlled `Params`, to all nodes in the DON, exactly as the question's threat model describes.

### Impact Explanation
This allows an unauthenticated gateway client to trigger `MethodSecretsGet` (secrets retrieval workflow) or `MethodCapabilityExec` (capability execution) requests that are broadcast to an entire DON's nodes without any caller-identity or signature check at the gateway layer, matching the "request impersonation" / "unauthorized job run" bounty impact class. Whether this becomes full exploit (e.g., actual secret exfiltration) depends on downstream node-side/enclave validation of the forwarded payload's authenticity, which is outside the gateway handler code reviewed here and could not be fully confirmed with the available index.

### Likelihood Explanation
Precondition is simply that the `confidentialrelay` service is configured/enabled — no credentials, allowlist membership, or valid signature are required by the gateway handler itself, since `HandleJSONRPCUserMessage` only validates request-ID length/non-emptiness before fanning out. This makes the request trivially repeatable by any network client that can reach the gateway's user-facing HTTP port.

### Recommendation
Add an explicit `req.Auth`/signature verification step (mirroring the vault handler's `requestProcessor.ProcessRequest`/`authorizer` pattern) inside `confidentialrelay.handler.HandleJSONRPCUserMessage` before `fanOutToNodes` is called, and consider hoisting a mandatory auth-verification step into the `Handler` interface contract (or a shared middleware in `gateway.ProcessRequest`) so future handlers cannot omit it.

### Proof of Concept
Go handler-level integration test plan for `core/services/gateway/handlers/confidentialrelay/handler_test.go`:
1. Construct a `handler` via `NewHandler` with a fake `DON` (`don.SendToNode` recorder) and no auth/allowlist configuration.
2. Build a `jsonrpc.Request[json.RawMessage]{Method: MethodSecretsGet, Auth: ""}` (empty/forged auth) with a valid unique ID.
3. Call `handler.HandleJSONRPCUserMessage(ctx, req, callback)`.
4. Assert: `don.SendToNode` was invoked for every DON member address with the unauthenticated request — i.e., no error/rejection occurred and no auth-check function was invoked — demonstrating the request was dispatched to the whole DON despite lacking any valid `req.Auth`.
5. Contrast with an equivalent test against `vault/handler.go` where the same empty-`Auth` request causes `h.requestProcessor.ProcessRequest` to return an authorization error before any `don.SendToNode` call, proving the inconsistency between handler implementations predicted by the interface's lack of a mandated auth check.

### Citations

**File:** core/services/gateway/handlers/handler.go (L39-43)
```go
	// Each user request is processed by a separate goroutine, which:
	//   1. calls HandleUserMessage
	//   2. waits on callbackCh with a timeout
	HandleJSONRPCUserMessage(ctx context.Context, jsonRequest jsonrpc.Request[json.RawMessage], callback Callback) error

```

**File:** core/services/gateway/handlers/vault/handler.go (L431-446)
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

	h.lggr.Debugw("handling authorized vault request", "method", req.Method, "requestID", req.ID, "authorizedOwner", authorizedOwner)
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L349-366)
```go
func (h *handler) HandleJSONRPCUserMessage(ctx context.Context, req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) error {
	if req.ID == "" {
		return errors.New("request ID cannot be empty")
	}
	if len(req.ID) > 200 {
		return errors.New("request ID is too long: " + strconv.Itoa(len(req.ID)) + ". max is 200 characters")
	}

	l := logger.With(h.lggr, "method", req.Method, "requestID", req.ID)
	l.Debugw("handling confidential relay request")

	ar, err := h.newActiveRequest(req, callback)
	if err != nil {
		return err
	}

	return h.fanOutToNodes(ctx, l, ar)
}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L593-595)
```go
// forwardBundle sends a previously-built bundle to the enclave. The gateway makes
// no trust decision; the enclave verifies signatures and reaches quorum.
func (h *handler) forwardBundle(ctx context.Context, l logger.Logger, ar *activeRequest, summary *BundleSummary) error {
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L618-652)
```go
func (h *handler) fanOutToNodes(ctx context.Context, l logger.Logger, ar *activeRequest) error {
	var (
		group      errgroup.Group
		nodeErrors atomic.Uint32
	)

	// Each send is bounded independently. A node whose websocket accepts no writes blocks
	// until its context is cancelled, and because the caller only reads the response callback
	// after this function returns, an unbounded send would hold the request open until the
	// client gives up, discarding a bundle that already reached quorum.
	sendCtx, cancel := context.WithTimeout(ctx, h.nodeSendTimeout)
	defer cancel()

	for _, node := range h.donConfig.Members {
		group.Go(func() error {
			err := h.don.SendToNode(sendCtx, node.Address, &ar.req)
			if err != nil {
				nodeErrors.Add(1)
				l.Errorw("error sending request to node", "node", node.Address, "error", err)
			}
			return nil
		})
	}

	_ = group.Wait()

	numNodeErrors := nodeErrors.Load()
	remainingPossibleResponses := len(h.donConfig.Members) - int(numNodeErrors)
	if remainingPossibleResponses < h.donConfig.F+1 && numNodeErrors > 0 {
		return h.sendResponseAndClearRequest(ctx, ar, h.constructErrorResponse(ar.req, api.FatalError, errors.New("failed to forward user request to nodes")))
	}

	l.Debugw("successfully forwarded request to relay nodes")
	return nil
}
```

**File:** core/services/gateway/gateway.go (L264-273)
```go
	startTime := time.Now()
	var method string
	callback := handlerscommon.NewCallback()
	if isLegacyRequest {
		method = msg.Body.Method
		err = h.HandleLegacyUserMessage(ctx, msg, callback)
	} else {
		method = jsonRequest.Method
		err = h.HandleJSONRPCUserMessage(ctx, jsonRequest, callback)
	}
```
