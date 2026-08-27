### Title
Legacy WebAPI trigger handler fails entire user request on single-node send failure - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.HandleLegacyUserMessage` in the WebAPI capability gateway handler fans a user's trigger request out to every DON member, but joins any single `SendToNode` error into the return value and propagates it, causing the gateway to immediately reject the whole request even if only one out of many DON nodes failed to receive it.

### Finding Description
`handler.HandleLegacyUserMessage` saves the user's callback and then loops over all DON members, aggregating every send error with `errors.Join`: [1](#0-0) 

The returned `err` is not filtered for "all failed" vs "one failed" — any non-nil error from any single `don.SendToNode` call causes the aggregate `err` to be non-nil.

That error is consumed directly by the internet-facing gateway entrypoint, `gateway.ProcessRequest`, which is invoked for every unprivileged HTTP client request: [2](#0-1) 

If `err != nil`, `ProcessRequest` returns `newError(..., api.HandlerError, ...)` to the client **immediately**, without ever calling `callback.Wait(ctx)`. This is the exact bug class from the referenced report: `RewardThrottle._sendToDistributor()` aborts an entire multi-recipient distribution because one recipient (`LinearDistributor`) is inactive, instead of continuing to work with the active/successful recipients. Here, the gateway aborts the entire multi-node broadcast and answers the client with a hard error because a single DON member's send failed (e.g., transient disconnect, full outbound queue, TCP write timeout to one node) — even though the request had already been dispatched successfully to the rest of the DON.

Other handlers in the same codebase demonstrate the correct pattern of tolerating partial failures and only erroring when a quorum/majority becomes unreachable, e.g. `confidentialrelay/handler.go` `fanOutToNodes` (only fails when `remainingPossibleResponses < F+1`): [3](#0-2) 

and `vault/handler.go` `fanOutToVaultNodes` (only fails when *all* nodes fail): [4](#0-3) 

The legacy WebAPI handler (`capabilities/handler.go`) and the dummy handler have not adopted this "fail only when quorum/all fail" tolerance, so a single flaky DON member is enough to break every request through this path: [5](#0-4) 

### Impact Explanation
An unprivileged client sending a legitimate `web_api_trigger`/legacy request through the gateway's user-facing HTTP endpoint will receive a `HandlerError` response for the entire request whenever exactly one DON member is temporarily unreachable or its send call errors — even though the majority of nodes may have received the request and would have produced a valid quorum response. This is a resilience/availability degradation of the gateway's core request-serving path: a single unhealthy node effectively denies service for that request to the legitimate caller, and the saved callback (`h.savedCallbacks[msg.Body.MessageId]`) is left orphaned since the caller never reaches `callback.Wait(ctx)`.

### Likelihood Explanation
This path is triggered by any normal, unprivileged user request to the WebAPI trigger service; no special permissions or race conditions are required. The only precondition is a transient failure to a single DON member (which is a normal operational occurrence — network blips, node restarts, temporary write timeouts), making this readily triggerable in production under ordinary node churn rather than requiring an attacker.

### Recommendation
Mirror the pattern already used in `confidentialrelay` and `vault` handlers: only treat the fan-out as failed when a configured threshold (e.g., all nodes, or fewer than `F+1`/quorum) of `SendToNode` calls fail, and otherwise proceed to await the callback so that responses from the successfully-reached nodes can still complete the user's request.

### Proof of Concept
1. Configure `handler.HandleLegacyUserMessage`'s underlying `don.SendToNode` such that it succeeds for members 2..N of the DON but returns an error for member 1 (simulating a single unhealthy/disconnected node), analogous to the existing test scaffolding in `TestHandlerReceiveHTTPMessageFromClient`. [6](#0-5) 
2. Send a legitimate `web_api_trigger` request via `gateway.ProcessRequest`.
3. Observe that `HandleLegacyUserMessage` returns a non-nil joined error (line 418) even though N-1 nodes were successfully sent the request. [7](#0-6) 
4. Observe that `gateway.ProcessRequest` short-circuits at line 274-276, returning a `HandlerError` response to the client without ever waiting on `callback.Wait(ctx)`, even though the majority of DON members already received and could have answered the request. [8](#0-7)

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-421)
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
}
```

**File:** core/services/gateway/gateway.go (L264-276)
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
	if err != nil {
		return newError(jsonRequest.ID, api.HandlerError, err.Error())
	}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L618-648)
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
```

**File:** core/services/gateway/handlers/vault/handler.go (L726-742)
```go
func (h *handler) fanOutToVaultNodes(ctx context.Context, l logger.Logger, ar *activeRequest) error {
	var nodeErrors []error
	for _, node := range h.donConfig.Members {
		err := h.don.SendToNode(ctx, node.Address, &ar.req)
		if err != nil {
			nodeErrors = append(nodeErrors, err)
			l.Errorw("error sending request to node", "node", node.Address, "error", err)
		}
	}

	if len(nodeErrors) == len(h.donConfig.Members) && len(nodeErrors) > 0 {
		return h.sendResponse(ctx, ar, h.errorResponse(ar.req, api.FatalError, errors.New("failed to forward user request to nodes"), nil))
	}

	l.Debugw("successfully forwarded request to Vault nodes")
	return nil
}
```

**File:** core/services/gateway/handlers/handler.dummy.go (L62-81)
```go
func (d *dummyHandler) HandleLegacyUserMessage(ctx context.Context, msg *api.Message, callback Callback) error {
	d.mu.Lock()
	d.savedCallbacks[msg.Body.MessageId] = &savedCallback{msg.Body.MessageId, callback}
	don := d.don
	d.mu.Unlock()
	params, err := json.Marshal(msg)
	if err != nil {
		return err
	}
	rawParams := json.RawMessage(params)
	req := &jsonrpc.Request[json.RawMessage]{
		Version: "2.0",
		ID:      msg.Body.MessageId,
		Method:  msg.Body.Method,
		Params:  &rawParams,
	}
	for _, member := range d.donConfig.Members {
		err = errors.Join(err, don.SendToNode(ctx, member.Address, req))
	}
	return err
```

**File:** core/services/gateway/handlers/capabilities/handler_test.go (L236-260)
```go
func TestHandlerReceiveHTTPMessageFromClient(t *testing.T) {
	handler, _, don, nodes := setupHandler(t)
	ctx := t.Context()
	msg := triggerRequest(t, nodes[0].PrivateKey, []string{"daily_price_update"}, "", "", "")
	codec := api.JsonRPCCodec{}

	t.Run("happy case", func(t *testing.T) {
		// sends to 2 dons
		don.On("SendToNode", mock.Anything, mock.Anything, mock.Anything).Run(func(args mock.Arguments) {
			nodeReq := nodeRequest(msg)
			require.Equal(t, nodeReq, args.Get(2))
		}).Return(nil).Once()
		don.On("SendToNode", mock.Anything, mock.Anything, mock.Anything).Run(func(args mock.Arguments) {
			nodeReq := nodeRequest(msg)
			require.Equal(t, nodeReq, args.Get(2))
		}).Return(nil).Once()

		cb := hc.NewCallback()
		err := handler.HandleLegacyUserMessage(ctx, msg, cb)
		require.NoError(t, err)

		resp, err := hc.ValidatedResponseFromMessage(msg)
		require.NoError(t, err)
		err = handler.HandleNodeMessage(ctx, resp, nodes[0].Address)
		require.NoError(t, err)
```
