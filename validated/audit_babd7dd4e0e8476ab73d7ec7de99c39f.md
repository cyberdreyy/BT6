#No Vulnerability found for this question.

**Analysis supporting this conclusion:**

The `nodeAddr` parameter passed into `HandleNodeMessage` is not attacker-controllable free text — it is derived deterministically from a verified cryptographic signature over the connection challenge during the WebSocket handshake, not from any client-suppliable field. [1](#0-0) 

Specifically, `nodeAddress` is computed as `"0x" + hex.EncodeToString(signer)` both at `StartHandshake` and at `FinalizeHandshake`, and `hex.EncodeToString` always produces lowercase hex digits, so the value is intrinsically normalized and cannot be supplied in mixed case by a connecting peer. [2](#0-1) [3](#0-2) 

The same lowercase `nodeAddress` value (sourced from the `donConnMgr.nodes` map key, itself built via `strings.ToLower(nodeConfig.Address)`) is what gets passed unchanged into `handler.HandleNodeMessage(ctx, &resp, nodeAddress)` in the read loop. [4](#0-3) 

While it's true that `perNodeRateLimiters` in `capabilities/v2/http_handler.go` and `confidentialrelay/handler.go`, and `nodeAddrToShard` built in `shard_endpoints.go`, are keyed directly off `config.NodeConfig.Address` without lowercasing, this is only reachable via operator-controlled DON configuration, not by an unauthenticated/unprivileged attacker. [5](#0-4) [6](#0-5) 

A mismatch here (if config casing differs from the connection manager's lowercase normalization) would produce a functional failure — every legitimate node message would fail the map lookup and hit `"received message from unexpected node"` uniformly — not an attacker-exploitable bypass or cross-node quota confusion, since:
1. Map lookups in Go require exact byte-for-byte key equality; there is no "fuzzy" or "aliasing" collision behavior that would let one string alias another node's bucket.
2. An unprivileged external attacker cannot inject an arbitrary-case `nodeAddr` value into this path at all — it is bound to the cryptographically verified DON node identity from the handshake, never attacker-supplied. [7](#0-6) 

This matches the question's own stated precondition that node messages are not attacker-reachable, and the potential casing inconsistency (if it exists) is a configuration/consistency concern between components rather than an authorization or rate-limit bypass exploitable by an unprivileged actor.

### Citations

**File:** core/services/gateway/connectionmanager.go (L136-153)
```go
func buildNodeStates(members []config.NodeConfig, donID string, lggr logger.Logger) (map[string]*nodeState, error) {
	nodes := make(map[string]*nodeState)
	for _, nodeConfig := range members {
		nodeAddress := strings.ToLower(nodeConfig.Address)
		if _, ok := nodes[nodeAddress]; ok {
			return nil, fmt.Errorf("duplicate node address %s in DON %s", nodeAddress, donID)
		}
		connWrapper := network.NewWSConnectionWrapper(lggr)
		if connWrapper == nil {
			return nil, fmt.Errorf("error creating WSConnectionWrapper for node %s", nodeAddress)
		}
		nodes[nodeAddress] = &nodeState{
			name: nodeConfig.Name,
			conn: connWrapper,
		}
	}
	return nodes, nil
}
```

**File:** core/services/gateway/connectionmanager.go (L196-223)
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
	if authHeaderElems.GatewayId != m.config.AuthGatewayId {
		return "", nil, network.ErrAuthInvalidGateway
	}
	nowTs := uint32(m.clock.Now().Unix())
	ts := authHeaderElems.Timestamp
	if ts < nowTs-m.config.AuthTimestampToleranceSec || nowTs+m.config.AuthTimestampToleranceSec < ts {
		return "", nil, network.ErrAuthInvalidTimestamp
	}
	attemptId, challenge, err = m.newAttempt(nodeState, nodeAddress, ts)
	if err != nil {
		return "", nil, err
	}
	return attemptId, challenge, nil
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

**File:** core/services/gateway/connectionmanager.go (L341-367)
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
```

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L138-146)
```go
	perNodeRateLimiters := make(map[string]limits.RateLimiter, len(members))
	for _, member := range members {
		var rl limits.RateLimiter
		rl, err = lf.MakeRateLimiter(cresettings.Default.GatewayHTTPPerNodeRate)
		if err != nil {
			return nil, fmt.Errorf("failed to create per-node rate limiter for %s: %w", member.Address, err)
		}
		perNodeRateLimiters[member.Address] = rl
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

**File:** core/services/gateway/handlers/capabilities/v2/shard_endpoints.go (L58-63)
```go
			for _, member := range shard.Nodes {
				if existing, ok := addrToShard[member.Address]; ok {
					return nil, nil, fmt.Errorf("node address %s appears in both %s and %s; shard memberships must be disjoint", member.Address, existing.donID, ep.donID)
				}
				addrToShard[member.Address] = ep
			}
```
