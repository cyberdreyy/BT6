This confirms the trust chain. `nodeAddress` passed into `HandleNodeMessage` originates from `readLoop(nodeAddress string, ...)` at [1](#0-0) , which is a fixed parameter bound at connection-establishment time to a specific per-node WebSocket connection object (`nodeState.conn`) — it is never parsed from the message body itself. That connection is only ever attached to a `nodeAddress` after a cryptographic challenge-response handshake in `StartHandshake`/`FinalizeHandshake`, where the address is derived from `common.ExtractSigner(response, ...)` and compared against the address that requested the challenge.

### Title
No exploitable vulnerability — nodeAddr in HandleNodeMessage is bound to a signature-verified per-connection identity, not attacker-controlled input - ([File: core/services/gateway/handlers/capabilities/v2/http_handler.go])

### Summary
The question's premise — that `nodeAddr` reaching `HandleNodeMessage` is an arbitrary, attacker-suppliable string with no cryptographic binding — is false in this codebase. The `donConnectionManager.readLoop` function passes a fixed `nodeAddress` string that was bound at handshake time via ECDSA signature verification, not extracted from the inbound JSON-RPC message content.

### Finding Description
`gatewayHandler.HandleNodeMessage(ctx, resp, nodeAddr string)` at [2](#0-1)  does look up `h.perNodeRateLimiters[nodeAddr]` using the passed-in string directly, and this file itself performs no signature check. However, the only caller of this method is `donConnectionManager.readLoop(nodeAddress string, nodeState *nodeState)` at [3](#0-2) , which invokes `handler.HandleNodeMessage(ctx, &resp, nodeAddress)` using the closure-captured `nodeAddress` — a value fixed per goroutine/connection, never read from `resp` or attacker-controlled message fields.

That `nodeAddress` is only ever associated with a live connection through the authenticated handshake sequence: `StartHandshake` extracts the DON member's address from a signed auth header via `network.UnpackSignedAuthHeader` at [4](#0-3) , and `FinalizeHandshake` verifies a challenge-response signature with `common.ExtractSigner(response, network.PackChallenge(&attempt.challenge))`, rejecting the connection unless `attempt.nodeAddress != "0x"+hex.EncodeToString(signer)` fails to hold, at [5](#0-4) . Only after this signature-verified handshake does `attempt.nodeState.conn.Reset(conn)` bind the physical WebSocket connection to that specific `nodeAddress`'s `nodeState`, and `readLoop` is spawned per `nodeAddress`/`nodeState` pair at DON connection manager start, at [6](#0-5) .

Thus an attacker cannot cause `HandleNodeMessage` to be invoked with a `nodeAddr` of their choosing simply by embedding it in a message; they would need to complete the ECDSA challenge-response handshake as that address, which requires possession of the corresponding private key — this is exactly the "malicious/leaked node key" scenario explicitly excluded by the audit rules (malicious-node / malicious-DON member paths are out of scope), not an unauthenticated-attacker-reachable path.

### Impact Explanation
None. There is no unauthenticated or under-privileged path to control `nodeAddr` independent of a verified per-connection identity.

### Likelihood Explanation
Not applicable — exploitation would require possessing a legitimate DON node's private signing key, which is excluded from this audit's threat model (malicious-node/DON-peer scenarios are explicitly rejected).

### Recommendation
No change required for this specific concern. If desired for defense-in-depth/clarity, a comment in `http_handler.go` near `HandleNodeMessage` could note that `nodeAddr` is trusted because it is supplied exclusively by the connection-manager layer after handshake-based signature verification (see `connectionmanager.go`), to make the trust boundary explicit for future reviewers.

### Proof of Concept
Not applicable/no PoC needed since the described attack surface does not exist as described: `readLoop` at [3](#0-2)  never derives `nodeAddress` from message content, and the existing tests such as `TestConnectionManager_FinalizeHandshake` at [7](#0-6)  already demonstrate that an invalid signer is rejected with `network.ErrChallengeInvalidSignature` before any connection (and thus any `nodeAddr`-tagged message dispatch) can occur.

### Citations

**File:** core/services/gateway/connectionmanager.go (L162-173)
```go
		for _, donConnMgr := range m.dons {
			donConnMgr.closeWait.Add(len(donConnMgr.nodes))
			for nodeAddress, nodeState := range donConnMgr.nodes {
				if err := nodeState.conn.Start(ctx); err != nil {
					return err
				}
				go donConnMgr.readLoop(nodeAddress, nodeState)
			}
			donConnMgr.closeWait.Add(len(donConnMgr.nodes))
			for nodeAddress, nodeState := range donConnMgr.nodes {
				go donConnMgr.nodeKeepalive(nodeAddress, nodeState, m.config.HeartbeatIntervalSec)
			}
```

**File:** core/services/gateway/connectionmanager.go (L196-210)
```go
func (m *connectionManager) StartHandshake(authHeader []byte) (attemptId string, challenge []byte, err error) {
	m.lggr.Debug("StartHandshake")
	authHeaderElems, signer, err := network.UnpackSignedAuthHeader(authHeader)
	if err != nil {
		return "", nil, errors.Join(network.ErrAuthHeaderParse, err)
	}
	nodeAddress := "0x" + hex.EncodeToString(signer)
	donConnMgr, ok := m.dons[authHeaderElems.DonId]
	if !ok {
		return "", nil, network.ErrAuthInvalidDonId
	}
	nodeState, ok := donConnMgr.nodes[nodeAddress]
	if !ok {
		return "", nil, network.ErrAuthInvalidNode
	}
```

**File:** core/services/gateway/connectionmanager.go (L241-253)
```go
func (m *connectionManager) FinalizeHandshake(attemptId string, response []byte, conn *websocket.Conn) error {
	m.lggr.Debugw("FinalizeHandshake", "attemptId", attemptId)
	m.connAttemptsMu.Lock()
	attempt, ok := m.connAttempts[attemptId]
	delete(m.connAttempts, attemptId)
	m.connAttemptsMu.Unlock()
	if !ok {
		return network.ErrChallengeAttemptNotFound
	}
	signer, err := common.ExtractSigner(response, network.PackChallenge(&attempt.challenge))
	if err != nil || attempt.nodeAddress != "0x"+hex.EncodeToString(signer) {
		return network.ErrChallengeInvalidSignature
	}
```

**File:** core/services/gateway/connectionmanager.go (L341-369)
```go
func (m *donConnectionManager) readLoop(nodeAddress string, nodeState *nodeState) {
	ctx, cancel := m.shutdownCh.NewCtx()
	defer cancel()
	for {
		select {
		case <-m.shutdownCh:
			m.closeWait.Done()
			return
		case item := <-nodeState.conn.ReadChannel():
			var resp jsonrpc.Response[json.RawMessage]
			err := json.Unmarshal(item.Data, &resp)
			if err != nil {
				m.lggr.Errorw("parse error when reading from node", "nodeAddress", nodeAddress, "err", err)
				break
			}
			handler, err := m.getHandler(resp.Method)
			if err != nil {
				m.lggr.Errorw("no handler for node message", "nodeAddress", nodeAddress, "method", resp.Method, "err", err)
				break
			}
			startTime := time.Now()
			err = handler.HandleNodeMessage(ctx, &resp, nodeAddress)
			m.gMetrics.RecordNodeMsgHandlerInvocation(ctx, nodeAddress, nodeState.name, err == nil)
			m.gMetrics.RecordNodeMsgHandlerDuration(ctx, nodeAddress, nodeState.name, time.Since(startTime), err == nil)
			if err != nil {
				m.lggr.Errorw("error when calling HandleNodeMessage", "err", err, "nodeAddress", nodeAddress, "nodeState", nodeState.name, "responseID", resp.ID)
			}
		}
	}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L238-246)
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
```

**File:** core/services/gateway/connectionmanager_test.go (L205-236)
```go
func TestConnectionManager_FinalizeHandshake(t *testing.T) {
	t.Parallel()

	config, nodes := newTestConfig(t, 4)
	clock := clockwork.NewFakeClock()
	mgr := newConnectionManager(t, config, clock)

	authHeaderElems := network.AuthHeaderElems{
		Timestamp: uint32(clock.Now().Unix()),
		DonId:     "my_don_1",
		GatewayId: "my_gateway_no_3",
	}

	// correct
	attemptId, challenge, err := mgr.StartHandshake(signAndPackAuthHeader(t, &authHeaderElems, nodes[0].PrivateKey))
	require.NoError(t, err)
	response, err := gc.SignData(nodes[0].PrivateKey, challenge)
	require.NoError(t, err)
	require.NoError(t, mgr.FinalizeHandshake(attemptId, response, nil))

	// invalid attempt
	err = mgr.FinalizeHandshake("fake_attempt", response, nil)
	require.ErrorIs(t, err, network.ErrChallengeAttemptNotFound)

	// invalid signature
	attemptId, challenge, err = mgr.StartHandshake(signAndPackAuthHeader(t, &authHeaderElems, nodes[0].PrivateKey))
	require.NoError(t, err)
	response, err = gc.SignData(nodes[1].PrivateKey, challenge)
	require.NoError(t, err)
	err = mgr.FinalizeHandshake(attemptId, response, nil)
	require.ErrorIs(t, err, network.ErrChallengeInvalidSignature)
}
```
