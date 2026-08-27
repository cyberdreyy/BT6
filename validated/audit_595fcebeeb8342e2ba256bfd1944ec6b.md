Interesting finding: `handleCapabilityExecute` in `core/capabilities/confidentialrelay/handler.go` is missing the `verifyWorkflowAuthorization` (PRIV-433) check that `handleSecretsGet` explicitly performs.

## Title
Missing Workflow-DON Authorization Check in `handleCapabilityExecute` Allows Owner/Workflow Impersonation - (File: core/capabilities/confidentialrelay/handler.go)

### Summary
`handleSecretsGet` and `handleCapabilityExecute` are sibling gateway-message handlers for the `EnclaveRelayHandler`, both reachable from an untrusted gateway client via `HandleGatewayMessage`. `handleSecretsGet` explicitly calls `h.verifyWorkflowAuthorization(localNode.WorkflowDON, params)` after attestation and enclave-config checks, ensuring the Workflow DON quorum actually authorized the claimed `Owner`/`WorkflowID` for that request [1](#0-0) . `handleCapabilityExecute` performs attestation verification and enclave-config verification but never calls an equivalent owner/workflow authorization check before dispatching the capability call and seeding `ctx` with attacker-supplied `params.OrgID`, `params.Owner`, and `params.WorkflowID` [2](#0-1) .

### Finding Description
`verifyWorkflowAuthorization` exists specifically to close the PRIV-433 gap: TEE attestation only proves the request came from genuine enclave code, not that the Workflow DON actually authorized the operation for the claimed owner/workflow [3](#0-2) . It verifies a 2*F+1 (or F+1) quorum of Workflow DON signatures over a `SignedComputeRequest` whose `PublicData` names the authorized owner and workflow, and rejects the request if `params.Owner`/`params.WorkflowID` don't match [4](#0-3) .

`handleCapabilityExecute` unmarshals `CapabilityRequestParams` (which includes `OrgID`, `Owner`, `WorkflowID`) directly from the untrusted gateway request, seeds them into `ctx` via `contexts.WithCRE`, and then only validates the attestation hash and enclave config match — never the workflow authorization [5](#0-4) . It then resolves the execution handler by `params.WorkflowID`/`params.ExecutionID` and calls `handler.CallCapability(ctx, sdkReq)` with the attacker-controlled `Owner`/`Org` tenant context [6](#0-5) .

This mirrors the `sellForLP` bug class exactly: two sibling functions in the same contract/handler share a security-critical validation modifier/check, but one path omits it, allowing the omitted check's protection to be bypassed for that specific method — here, the missing owner/workflow authorization check on `MethodCapabilityExec`.

### Impact Explanation
A compromised or malicious TEE host (or anyone able to produce a genuinely-attested `CapabilityExec` request, since attestation only proves code integrity, not caller authorization) can invoke `CallCapability` while asserting an arbitrary `Owner`/`OrgID`/`WorkflowID` in the tenant context, without the Workflow DON ever having signed off on that specific owner/workflow pairing. This is a request-impersonation / cross-tenant authorization bypass: capability execution proceeds under a falsely-claimed owner identity, which the code's own comments (PRIV-433, PRIV-458) identify as exactly the scenario `verifyWorkflowAuthorization` is meant to prevent.

### Likelihood Explanation
The `handleCapabilityExecute` path is reachable directly from `HandleGatewayMessage` for any request routed by the gateway with `method = confidentialrelaytypes.MethodCapabilityExec` [7](#0-6) . No additional check enforces that the claimed owner/workflow was actually authorized by the Workflow DON quorum before capability execution, so exploitation only requires a validly-attested enclave request (which the enclave, or a party controlling its input path, can produce) with forged `Owner`/`OrgID`/`WorkflowID` fields.

### Recommendation
Add an equivalent `verifyWorkflowAuthorization`-style check to `handleCapabilityExecute` before resolving the execution handler and calling `CallCapability`, verifying the Workflow DON quorum's `SignedComputeRequest`s authorize the claimed `params.Owner`/`params.OrgID`/`params.WorkflowID`, mirroring the check already present in `handleSecretsGet`.

### Proof of Concept
1. As an attacker able to reach the gateway with a validly-attested `CapabilityExec` request (e.g., via a compromised enclave host producing genuine Nitro attestation over attacker-chosen `CapabilityRequestParams`), craft a request with `params.Owner = "0xVictim"`, `params.OrgID = "victim-org"`, and a `WorkflowID`/`ExecutionID` pair that resolves to an execution handler on this node.
2. Send the request as `MethodCapabilityExec` through the gateway connector to `HandleGatewayMessage`.
3. The handler validates the attestation hash and enclave config [8](#0-7)  — both pass since the attestation is genuine — but never checks that the Workflow DON actually signed off on `Owner="0xVictim"` for this workflow.
4. `contexts.WithCRE` seeds the impersonated owner/org into `ctx`, and `handler.CallCapability(ctx, sdkReq)` executes under that falsely-claimed tenant identity [9](#0-8) .

### Citations

**File:** core/capabilities/confidentialrelay/handler.go (L251-255)
```go
	switch req.Method {
	case confidentialrelaytypes.MethodSecretsGet:
		response = h.handleSecretsGet(ctx, gatewayID, req)
	case confidentialrelaytypes.MethodCapabilityExec:
		response = h.handleCapabilityExecute(ctx, gatewayID, req)
```

**File:** core/capabilities/confidentialrelay/handler.go (L308-314)
```go
	// Beyond attestation, verify the Workflow DON authorized this request: the enclave
	// forwards the Workflow-DON-signed compute requests (a 2*F+1 quorum), whose PublicData
	// names the authorized owner. A TEE breach passes attestation but cannot forge a Workflow
	// DON quorum over a different owner (PRIV-433).
	if err = h.verifyWorkflowAuthorization(localNode.WorkflowDON, params); err != nil {
		return h.errorResponse(ctx, gatewayID, req, jsonrpc.ErrInvalidParams, err)
	}
```

**File:** core/capabilities/confidentialrelay/handler.go (L434-495)
```go
func (h *Handler) handleCapabilityExecute(ctx context.Context, gatewayID string, req *jsonrpc.Request[json.RawMessage]) *jsonrpc.Response[json.RawMessage] {
	if req.Params == nil {
		return h.errorResponse(ctx, gatewayID, req, jsonrpc.ErrInvalidParams, errors.New("missing params"))
	}
	var params confidentialrelaytypes.CapabilityRequestParams
	if err := json.Unmarshal(*req.Params, &params); err != nil {
		return h.errorResponse(ctx, gatewayID, req, jsonrpc.ErrInvalidParams, err)
	}

	// The enclave's capability calls arrive as fresh gateway messages rather than
	// through the workflow engine, so ctx carries none of the CRE tenants the engine
	// seeds.
	//
	// Seeded as soon as the params are parsed so every ctx use below carries the
	// tenant.
	ctx = contexts.WithCRE(ctx, contexts.CRE{
		Org:      params.OrgID,
		Owner:    params.Owner,
		Workflow: params.WorkflowID,
	})

	att := params.Attestation
	params.Attestation = ""
	if err := h.verifyAttestationHash(ctx, att, params, confidentialrelaytypes.DomainCapabilityExec); err != nil {
		return h.errorResponse(ctx, gatewayID, req, jsonrpc.ErrInternal, err)
	}

	// Verify the enclave's reported config matches the onchain DON state, same as
	// handleSecretsGet (PRIV-458): the Nitro attestation binds the request hash but
	// not the config value, so a malicious host could otherwise produce a
	// genuinely-attested request over a forged enclave config.
	localNode, err := h.capRegistry.LocalNode(ctx)
	if err != nil {
		return h.errorResponse(ctx, gatewayID, req, jsonrpc.ErrInternal, fmt.Errorf("failed to get local node: %w", err))
	}
	if err = h.verifyEnclaveConfigMatchesDON(localNode, params.EnclaveConfig); err != nil {
		return h.errorResponse(ctx, gatewayID, req, jsonrpc.ErrInternal, err)
	}

	payloadBytes, err := base64.StdEncoding.DecodeString(params.Payload)
	if err != nil {
		return h.errorResponse(ctx, gatewayID, req, jsonrpc.ErrInvalidParams, fmt.Errorf("failed to decode payload: %w", err))
	}

	sdkReq := &sdkpb.CapabilityRequest{}
	if err = proto.Unmarshal(payloadBytes, sdkReq); err != nil {
		return h.errorResponse(ctx, gatewayID, req, jsonrpc.ErrInvalidParams, fmt.Errorf("failed to unmarshal capability request: %w", err))
	}

	// Resolve the execution handler only after attestation and enclave-config
	// verification, so an unverified callback cannot make the node park a waiter.
	// The enclave's callback can beat this node's own execution start (start-edge
	// race); a bounded wait lets a straggler register and sign instead of dropping
	// below relay quorum (see handleSecretsGet).
	waitCtx, cancel := context.WithTimeout(ctx, h.getExecutionWait)
	defer cancel()
	handler, ok := h.executionHandlers.GetExecutionWithWait(waitCtx, params.WorkflowID, params.ExecutionID)
	if !ok {
		return h.errorResponse(ctx, gatewayID, req, jsonrpc.ErrInvalidParams, fmt.Errorf("execution handler for workflow %s execution %s not found", params.WorkflowID, params.ExecutionID))
	}

	capResp, execErr := handler.CallCapability(ctx, sdkReq)
```

**File:** core/capabilities/confidentialrelay/handler.go (L653-666)
```go
// verifyWorkflowAuthorization is the PRIV-433 check beyond attestation. Attestation only
// proves the request came from genuine enclave code; it does not prove the Workflow DON
// authorized fetching this owner's secrets. A compromised TEE would still pass attestation
// while self-asserting a victim's owner.
//
// The enclave forwards the Workflow-DON-signed compute requests it executed (a 2*F+1 quorum,
// where F is the Workflow DON fault tolerance). Each node signs the same ComputeRequest.Hash();
// we reconstruct that hash, verify each signature against the onchain Workflow DON signer set,
// and require the quorum of unique signers. The signed PublicData names the authorized owner
// and workflow, which must match this request. A breached enclave cannot forge a Workflow DON
// quorum over a different owner.
//
// All failures here are client errors: the request is unauthorized. The caller fetches the
// Workflow DON (a server-side concern) and passes it in, so registry failures stay internal.
```

**File:** core/capabilities/confidentialrelay/handler.go (L700-716)
```go

	// The signed request authorizes a specific owner and workflow; the secrets request must
	// match both, or a breached enclave could fetch another owner's secrets.
	var execution confidentialworkflow.WorkflowExecution
	if err := proto.Unmarshal(params.SignedComputeRequests[0].PublicData, &execution); err != nil {
		return fmt.Errorf("failed to unmarshal workflow execution from public data: %w", err)
	}
	if !common.IsHexAddress(params.Owner) || !common.IsHexAddress(execution.GetOwner()) {
		return errors.New("invalid owner address")
	}
	if common.HexToAddress(execution.GetOwner()) != common.HexToAddress(params.Owner) {
		return fmt.Errorf("owner not authorized: request %q vs signed %q", params.Owner, execution.GetOwner())
	}
	if execution.GetWorkflowId() != params.WorkflowID {
		return fmt.Errorf("workflow_id not authorized: request %q vs signed %q", params.WorkflowID, execution.GetWorkflowId())
	}
	return nil
```
