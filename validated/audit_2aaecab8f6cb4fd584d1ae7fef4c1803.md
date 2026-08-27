### Title
Missing role-based authorization on `POST /v2/execute_capability` allows run-role users to execute arbitrary registered capabilities - ([File: core/web/capability_controller.go])

### Summary
`CapabilityController.ExecuteCapability` accepts an arbitrary `capabilityName` and opaque `capabilityRequest` payload and directly invokes `capability.Execute()` against whatever is returned from `GetCapabilitiesRegistry().GetExecutable()`, with no internal role or scope check. The handler performs no differentiation between "run" and "edit" level actions of the underlying capability, so any authenticated user whose session is only permitted to trigger job runs can instead invoke any capability registered on the node, including ones intended to require edit/admin authorization.

### Finding Description
`ExecuteCapability` in `core/web/capability_controller.go` (lines 26-68) does the following with no authorization logic of its own: [1](#0-0) 
- Binds `CapabilityRequestOuter{CapabilityName, CapabilityRequest}` straight from the JSON body.
- Looks up `capabilityRegistry.GetExecutable(ctx, capabilityRequestOuter.CapabilityName)` — any capability name registered in the node's `CapabilitiesRegistry`, not scoped to a fixed "job-run" capability.
- Unmarshals the attacker-supplied `capabilityRequest` bytes into a `pb.CapabilityRequest` and calls `capability.Execute(ctx, capabilityRequest)` directly.

There is no check inside this function comparing the caller's role (view/run/edit/admin) against the semantics of the target capability. All authorization for this endpoint is therefore delegated entirely to whatever middleware wraps it in `core/web/router.go`'s `v2Routes` registration. I was not able to retrieve the exact `v2Routes` capability-controller registration block in this session (tool budget exhausted), so I cannot confirm from source whether that registration attaches a role-specific middleware (e.g. `RequiresEditRole`) versus only a generic session-authentication check plus `build.IsDev()`. This is a material gap: the vulnerability's validity hinges on that exact gating, which the audit prompt asserts is "gated only by build.IsDev()" but which I could not independently verify by reading the route table.

Assuming the premise in the question is accurate (route requires only an authenticated session + dev build, not a specific role tier), the flaw is real: because `ExecuteCapability` treats all capabilities uniformly and performs no per-capability action-scoping, a run-role session could execute any capability registered in the node — potentially including capabilities whose write/administrative side effects go well beyond "triggering a job run" (e.g. writing to external systems, mutating on-chain state, or other action-type capabilities), which is an authorization-exactness violation if the intended design ties role tiers to action severity.

### Impact Explanation
If confirmed, scoped impact is privilege escalation: a run-role authenticated user gains the ability to invoke arbitrary registered node capabilities with attacker-controlled request payloads, effectively obtaining edit/admin-equivalent execution power over capability actions without holding that role. This falls in the "role/authorization bypass leading to unauthorized action" bounty impact class.

### Likelihood Explanation
Preconditions: (1) node built/run in dev mode (`build.IsDev()` true), (2) attacker holds a valid run-role authenticated session or token, (3) at least one capability is registered whose `Execute` performs an action beyond simple job-run triggering. Under the premise stated in the audit question, no additional privilege is needed beyond a run-role session, making this straightforward and repeatable for anyone with that minimal credential in a dev build. However, since I could not directly verify the router-level gating code, likelihood is conditional on that premise being accurate.

### Recommendation
- Add explicit role/action-scope authorization inside `ExecuteCapability` (or via router middleware) that maps the caller's role to the permitted capability action types (e.g., only allow run-role callers to invoke capabilities flagged as "trigger"/read-type, reject edit/action-type capabilities).
- Do not rely solely on `build.IsDev()` as a gate for a route that can invoke arbitrary node capabilities; apply the same `RequiresEditRole`/`RequiresRunRole` wrappers used elsewhere in `core/web/router.go` consistently, keyed to the capability's declared action semantics rather than a blanket check.
- Validate `capabilityRequestOuter.CapabilityName` against an explicit allowlist of capabilities safe for run-role invocation.

### Proof of Concept
Handler-level integration test plan (Go, using existing patterns in `core/web/capability_controller_test.go`):
1. Register two fake capabilities in the `CapabilitiesRegistry`: one tagged as a benign "run/trigger" action, one tagged as an "edit/admin" action (e.g., a capability that would mutate state).
2. Create two test HTTP sessions: one with `run` role, one with `edit` role, using the app's normal session/auth test helpers.
3. As the run-role session, POST to `/v2/execute_capability` with `capabilityName` set to the "edit" capability's name and a valid `capabilityRequest` payload.
4. Assert expected: `403 Forbidden`.
5. Actual (per premise under audit): `200 OK` with `capabilityResponse` populated, showing the edit-type capability executed successfully despite the caller only holding run role.
6. Repeat with the "run/trigger" capability as a control, expecting `200 OK` for both role sessions to confirm the differentiation is role-based rather than capability-existence-based.

### Citations

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
