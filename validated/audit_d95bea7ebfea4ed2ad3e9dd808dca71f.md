### Title
Missing role-based access control on `ExternalInitiatorsController.Index` discloses all External Initiator `AccessKey`/`OutgoingToken` credentials to low-privileged (View/Run role) authenticated users - ([File: core/web/external_initiators_controller.go])

### Summary
The `GET /v2/external_initiators` route is registered without any role-gating middleware, unlike its sibling `Create`/`Destroy` endpoints which are wrapped in `auth.RequiresEditRole`. Any authenticated identity — including the lowest-privilege View or Run role — can call `Index` and receive the full global list of External Initiators, including their `AccessKey` and `OutgoingToken` fields.

### Finding Description
In `core/web/router.go`, the External Initiator routes are defined as: [1](#0-0) 
`GET /external_initiators` (`eia.Index`) has no role wrapper, while `POST` (`Create`) and `DELETE` (`Destroy`) are explicitly wrapped with `auth.RequiresEditRole`. The `authv2` group itself only requires `auth.Authenticate(...AuthenticateByToken, AuthenticateBySession)` [2](#0-1) , meaning any successfully authenticated session/API-token user — regardless of assigned `UserRole` (View, Run, Edit, or Admin) — passes through to `Index`.

`ExternalInitiatorsController.Index` then unconditionally queries the entire table via `eic.App.BridgeORM().ExternalInitiators(ctx, offset, size)` with no per-caller/per-role filter, and serializes every returned record through `presenters.NewExternalInitiatorResource`: [3](#0-2) [4](#0-3) 

The resource includes `AccessKey` (the EI's inbound authentication key) and `OutgoingToken` (used by the node to authenticate to the external initiator's webhook), both of which are credential material. No `RequiresEditRole`/`RequiresRunRole`/`RequiresAdminRole` wrapper protects this route, so the same information that requires Edit role to create/delete is readable by any lower-privileged authenticated caller (View or Run role) with no additional check.

Note: the premise of the question — that External Initiators are owned per-tenant/per-admin and thus this is a "cross-tenant ISOLATION" bug — does not hold in this codebase. External Initiators are global node-level entities (there is no ownership/tenant column on `bridges.ExternalInitiator`), so there is no multi-tenant boundary to violate. The real, concrete issue is a missing role check causing privilege escalation: a low-privileged role (View/Run) can read sensitive credential fields that the system's own design otherwise gates behind Edit role for mutation operations.

### Impact Explanation
A View or Run role user (or an API token scoped to those roles) — an attacker explicitly in-scope per the rules — can enumerate all External Initiators on the node and obtain their `AccessKey` and `OutgoingToken`. `AccessKey`/`Secret` pairs are used by the node to authenticate inbound EI-triggered job runs (`AuthenticateExternalInitiator` middleware, used for `POST /v2/jobs/:ID/runs`), so possession of the `AccessKey` moves an attacker closer to impersonating an External Initiator's identity for triggering job runs, and `OutgoingToken` may be used by outbound integrations. This constitutes unauthorized credential disclosure and role/authorization bypass, matching the "key or secret disclosure" / "role bypass" bounty impact class.

### Likelihood Explanation
Low precondition: any authenticated user with the lowest role (View or Run) via session cookie or API token can call this endpoint immediately — no additional exploitation steps, timing, or race conditions required. It is fully repeatable and requires only that the node has at least one existing External Initiator and one non-Edit/non-Admin user account (a realistic multi-user node configuration).

### Recommendation
Gate `GET /v2/external_initiators` with the same authorization level required to manage External Initiators (e.g., `auth.RequiresEditRole` or at minimum `auth.RequiresRunRole` consistent with the sensitivity of `AccessKey`/`OutgoingToken`), matching the protection already applied to `Create`/`Destroy`. Alternatively, redact `AccessKey`/`OutgoingToken` from the `ExternalInitiatorResource` presenter for non-privileged roles.

### Proof of Concept
Go handler-level integration test plan (extending `core/web/external_initiators_controller_test.go`):
1. Create an app/session with two users: one `UserRoleAdmin` (or Edit) who creates an External Initiator via `POST /v2/external_initiators`, and one `UserRoleView` (or `UserRoleRun`) test user with only a session/API token.
2. As the admin, `POST /v2/external_initiators` to create EI `foo`; capture returned `AccessKey`/`OutgoingToken` from the create response.
3. Authenticate as the View/Run-role user (session cookie or scoped API token), call `GET /v2/external_initiators`.
4. Assert HTTP 200 (not 401/403) and that the response body's `externalInitiators[].accessKey` / `.outgoingToken` match the values from step 2 — proving a low-privileged, non-Edit role can read EI credentials that only Edit-role callers are supposed to manage.
5. Compare against `POST`/`DELETE` on the same resource with the View/Run-role token, expecting 401/403 (`RequiresEditRole` correctly blocks mutation), highlighting the inconsistency: read of secrets is unprotected while write is protected.

### Citations

**File:** core/web/router.go (L245-248)
```go
	authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
```

**File:** core/web/router.go (L263-266)
```go
		eia := ExternalInitiatorsController{app}
		authv2.GET("/external_initiators", paginatedRequest(eia.Index))
		authv2.POST("/external_initiators", auth.RequiresEditRole(eia.Create))
		authv2.DELETE("/external_initiators/:Name", auth.RequiresEditRole(eia.Destroy))
```

**File:** core/web/external_initiators_controller.go (L50-59)
```go
func (eic *ExternalInitiatorsController) Index(c *gin.Context, size, page, offset int) {
	ctx := c.Request.Context()
	externalInitiators, count, err := eic.App.BridgeORM().ExternalInitiators(ctx, offset, size)
	resources := make([]presenters.ExternalInitiatorResource, 0, len(externalInitiators))
	for _, initiator := range externalInitiators {
		resources = append(resources, presenters.NewExternalInitiatorResource(initiator))
	}

	paginatedResponse(c, "externalInitiators", size, page, resources, count, err)
}
```

**File:** core/web/presenters/external_initiators.go (L57-77)
```go
type ExternalInitiatorResource struct {
	JAID
	Name          string         `json:"name"`
	URL           *models.WebURL `json:"url"`
	AccessKey     string         `json:"accessKey"`
	OutgoingToken string         `json:"outgoingToken"`
	CreatedAt     time.Time      `json:"createdAt"`
	UpdatedAt     time.Time      `json:"updatedAt"`
}

func NewExternalInitiatorResource(ei bridges.ExternalInitiator) ExternalInitiatorResource {
	return ExternalInitiatorResource{
		JAID:          NewJAID(strconv.FormatInt(ei.ID, 10)),
		Name:          ei.Name,
		URL:           ei.URL,
		AccessKey:     ei.AccessKey,
		OutgoingToken: ei.OutgoingToken,
		CreatedAt:     ei.CreatedAt,
		UpdatedAt:     ei.UpdatedAt,
	}
}
```
