### Title
Run-role users can execute arbitrary registered capabilities without per-capability authorization checks - ([File: core/web/router.go])

### Summary
The `/v2/execute_capability` route is gated only by `auth.RequiresRunRole` and a `build.IsDev()` build flag, and forwards directly into `CapabilityController.ExecuteCapability`, which calls `capabilityRegistry.GetExecutable` and `capability.Execute` for any `capabilityName` supplied in the request body with no check that the authenticated user/caller is authorized for that specific capability or the workflow it belongs to. Any credential holding "run" role or above (including any external-initiator credential, which is automatically granted `UserRoleRun` per `auth.AuthenticateExternalInitiator`) can invoke any registered capability, including ones capable of performing on-chain writes.

### Finding Description
`v2Routes` in [1](#0-0)  registers the route conditionally on `build.IsDev()`, wrapped only in `auth.RequiresRunRole`:
```go
if build.IsDev() {
    capContr := CapabilityController{app}
    authv2.POST("/execute_capability", auth.RequiresRunRole(capContr.ExecuteCapability))
}
```
`auth.RequiresRunRole` in [2](#0-1)  only checks that `user.Role != clsessions.UserRoleView` — i.e., it allows any authenticated user with `run`, `edit`, or `admin` role, and it performs no per-resource authorization: it has no notion of "which capability" or "which workflow" the caller is entitled to invoke.

`CapabilityController.ExecuteCapability` in [3](#0-2)  takes an attacker-controlled `CapabilityRequestOuter{CapabilityName, CapabilityRequest}` from the JSON body, looks up any capability by name via `capabilityRegistry.GetExecutable(ctx, capabilityRequestOuter.CapabilityName)`, and calls `capability.Execute(ctx, capabilityRequest)` directly — there is no verification that the caller (or the deserialized `capabilityRequest.Metadata.WorkflowID`/`WorkflowOwner`) matches any authorization binding for that capability. The `pb.UnmarshalCapabilityRequest` deserializes attacker-supplied metadata (including `WorkflowID`, `WorkflowOwner`, etc. — see fields referenced in [4](#0-3) ), which is then passed straight to the capability's `Execute` method, so the caller fully controls which workflow identity the capability execution is attributed to.

By contrast, the legitimate/internal capability execution path in `core/capabilities/remote/executable/request/server_request.go` binds workflow/DON identity to the authenticated peer (`workflowDONBindingGate`, `callingDonID` checks — see [5](#0-4) ) before invoking `capability.Execute`. The HTTP `/v2/execute_capability` path has no equivalent binding.

The only mitigating factor is that the route is registered exclusively `if build.IsDev()`; whether a "non-production but externally reachable" build sets this flag true is a build-configuration matter I could not fully verify — I found `build.IsDev()` referenced in [6](#0-5)  and in [7](#0-6)  and [8](#0-7) , but the `build` package implementation itself (defining what determines dev vs. prod, e.g., ldflags at compile time) was not present in the indexed content, so I cannot confirm precisely which build tags/flags control it or how commonly dev builds are deployed and externally reachable in practice.

### Impact Explanation
If a build is compiled/run with `build.IsDev()` true and is network-reachable, any credential with run-role (a low-privilege role, and one automatically granted to any external-initiator token per [9](#0-8) ) can invoke arbitrary registered capabilities by name, including ones capable of triggering on-chain transactions/target capabilities, with fully attacker-supplied `CapabilityRequest` metadata (workflow ID/owner, inputs, config). This maps to unauthorized action/fund movement and authorization-bypass bounty impact classes, since there is no per-capability ACL binding the caller to a specific capability or workflow — a run-role user can act as if they owned any workflow's capability binding.

### Likelihood Explanation
Preconditions: (1) the node must be built/running with `build.IsDev()` == true, and (2) the attacker needs any run-role-or-above credential (API token or session), or an external-initiator credential (which is automatically treated as run-role). Given the code only gates on role, not capability ownership, exploitation is straightforward and repeatable once those two preconditions hold. The remaining uncertainty is how likely/common it is for a "non-production but externally reachable" deployment to have `IsDev()` return true — this depends on the `build` package's implementation (not available in the index) and deployment practices, which I could not fully confirm.

### Recommendation
Add per-capability/per-workflow authorization to `CapabilityController.ExecuteCapability`: verify that the authenticated caller (or the credential's associated workflow/owner identity) is permitted to invoke the specific `CapabilityName` and that the `WorkflowID`/`WorkflowOwner` in the deserialized `CapabilityRequest` matches an authorized binding, rather than relying solely on the coarse `RequiresRunRole` role check and the `build.IsDev()` compile-time gate. Consider removing this route entirely from production-reachable builds, or restricting it to loopback/internal-only listeners, in addition to adding capability-level ACL enforcement mirroring the DON-binding checks performed in `server_request.go`.

### Proof of Concept
```go
func TestCapabilityController_ExecuteCapability_NoOwnershipCheck(t *testing.T) {
    // Setup: mock CapabilitiesRegistry with an on-chain-write-capable
    // capability "target-onchain-write@1.0.0" registered for workflow "victim-workflow".
    mockApp := appmocks.NewApplication(t)
    mockRegistry := registrymock.NewCapabilitiesRegistry(t)
    mockApp.EXPECT().GetCapabilitiesRegistry().Return(&capabilities.Registry{
        CapabilitiesRegistryBase: mockRegistry,
    })
    executableCap := capmock.NewExecutableCapability(t)
    mockRegistry.EXPECT().GetExecutable(mock.Anything, "target-onchain-write@1.0.0").Return(executableCap, nil)

    // Attacker supplies a CapabilityRequest with WorkflowID/Owner belonging to
    // a workflow the caller does not own.
    executableCap.EXPECT().Execute(mock.Anything, mock.MatchedBy(func(req capabilities.CapabilityRequest) bool {
        return req.Metadata.WorkflowID == "victim-workflow"
    })).Return(commoncap.CapabilityResponse{}, nil)

    // Build router with only RequiresRunRole auth (simulate a run-role session/token).
    engine := gin.New()
    controller := web.CapabilityController{App: mockApp}
    engine.POST("/v2/execute_capability", auth.RequiresRunRole(controller.ExecuteCapability))

    reqBody := web.CapabilityRequestOuter{
        CapabilityName:    "target-onchain-write@1.0.0",
        CapabilityRequest: marshalRequestWithWorkflow(t, "victim-workflow", "victim-owner"),
    }
    reqJSON, _ := json.Marshal(reqBody)
    req := httptest.NewRequest(http.MethodPost, "/v2/execute_capability", bytes.NewBuffer(reqJSON))
    // simulate session with run-role user in context via test middleware
    w := httptest.NewRecorder()
    engine.ServeHTTP(w, req)

    // EXPECTED (secure) behavior: 403 Forbidden — caller is not authorized for
    // "victim-workflow" / this capability.
    // ACTUAL (current) behavior: 200 OK — capability executes on behalf of
    // an arbitrary workflow/owner supplied by the run-role caller.
    assert.Equal(t, http.StatusForbidden, w.Code) // fails today: w.Code == 200
}
```
This demonstrates that the current implementation grants blanket run-role access to any registered capability with attacker-controlled workflow/owner metadata, with no per-capability ownership or allowlist enforcement, contrary to the expected authorization boundary.

### Citations

**File:** core/web/router.go (L304-308)
```go
		if build.IsDev() {
			capContr := CapabilityController{app}
			authv2.POST("/execute_capability", auth.RequiresRunRole(capContr.ExecuteCapability))
		}

```

**File:** core/web/auth/auth.go (L145-150)
```go
	// External initiator endpoints (wrapped with AuthenticateExternalInitiator) inherently assume the role
	// of 'run' (required to trigger job runs)
	c.Set(SessionExternalInitiatorKey, ei)
	c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})

	return nil
```

**File:** core/web/auth/auth.go (L200-217)
```go
// RequiresRunRole extracts the user object from the context, and asserts the user's role is at least
// 'run'
func RequiresRunRole(handler func(*gin.Context)) func(*gin.Context) {
	return func(c *gin.Context) {
		user, ok := GetAuthenticatedUser(c)
		if !ok {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("not a valid session"))
			return
		}
		if user.Role == clsessions.UserRoleView {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("Unauthorized"))
			return
		}
		handler(c)
	}
}
```

**File:** core/web/capability_controller.go (L26-68)
```go
func (cc *CapabilityController) ExecuteCapability(c *gin.Context) {
	body := c.Request.Body
	if body == nil {
		jsonAPIError(c, http.StatusBadRequest, errors.New("missing request body"))
		return
	}

	capabilityRegistry := cc.App.GetCapabilitiesRegistry()
	if capabilityRegistry == nil {
		jsonAPIError(c, http.StatusInternalServerError, errors.New("capability registry not initialized"))
		return
	}
	var capabilityRequestOuter CapabilityRequestOuter
	if err := c.BindJSON(&capabilityRequestOuter); err != nil {
		jsonAPIError(c, http.StatusBadRequest, err)
		return
	}

	capability, err := capabilityRegistry.GetExecutable(c.Request.Context(), capabilityRequestOuter.CapabilityName)
	if err != nil {
		jsonAPIError(c, http.StatusNotFound, err)
		return
	}

	capabilityRequest, err := pb.UnmarshalCapabilityRequest(capabilityRequestOuter.CapabilityRequest)
	if err != nil {
		jsonAPIError(c, http.StatusBadRequest, err)
		return
	}

	resp, err := capability.Execute(c.Request.Context(), capabilityRequest)
	if err != nil {
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}

	responseBytes, err := pb.MarshalCapabilityResponse(resp)
	if err != nil {
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}

	c.JSON(http.StatusOK, gin.H{"capabilityResponse": responseBytes})
```

**File:** core/services/workflows/v2/capability_executor.go (L215-233)
```go
	capReq := capabilities.CapabilityRequest{
		Payload:      request.Payload,
		Method:       request.Method,
		CapabilityId: request.Id,
		Metadata: capabilities.RequestMetadata{
			WorkflowOwner:            c.cfg.WorkflowOwner,
			WorkflowID:               c.cfg.WorkflowID,
			WorkflowExecutionID:      c.WorkflowExecutionID,
			WorkflowName:             c.cfg.WorkflowName.Hex(),
			WorkflowDonID:            localNode.WorkflowDON.ID,
			WorkflowDonConfigVersion: pinnedWorkflowDonConfigVersion,
			ReferenceID:              strconv.Itoa(int(request.CallbackId)),
			DecodedWorkflowName:      c.cfg.WorkflowName.String(),
			SpendLimits:              spendLimits,
			WorkflowTag:              c.cfg.WorkflowTag,
			ExecutionTimestamp:       c.ExecutionTimestamp,
		},
		Config: values.EmptyMap(),
	}
```

**File:** core/capabilities/remote/executable/request/server_request.go (L395-409)
```go
	// When enabled, bind the caller-supplied WorkflowDonID to the authenticated
	// calling DON so it cannot be spoofed. All F+1 aggregated requests share this
	// payload (WorkflowDonID is part of the request hash), so a single check here
	// covers the quorum. The gate is guaranteed non-nil by NewServerRequest.
	enabled, gerr := workflowDONBindingGate.Limit(ctx)
	if gerr != nil {
		lggr.Errorw("failed to evaluate workflow DON binding gate", "err", gerr)
		return nil, errors.New("failed to evaluate workflow DON binding gate")
	}
	if enabled && capabilityRequest.Metadata.WorkflowDonID != callingDonID {
		lggr.Errorw("workflow DON ID in request metadata does not match calling DON",
			"metadataWorkflowDonID", capabilityRequest.Metadata.WorkflowDonID, "callingDonID", callingDonID)
		return nil, fmt.Errorf("workflow DON ID %d in request metadata does not match calling DON ID %d",
			capabilityRequest.Metadata.WorkflowDonID, callingDonID)
	}
```

**File:** core/services/chainlink/config_insecure.go (L1-1)
```go
package chainlink
```

**File:** core/cmd/shell_local.go (L1-1)
```go
package cmd
```
