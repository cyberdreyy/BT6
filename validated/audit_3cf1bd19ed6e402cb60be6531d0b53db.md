Confirmed: the `GET /v2/external_initiators` route is mounted without any `RequiresEditRole`/`RequiresAdminRole`/`RequiresViewRole` wrapper — only the general `authv2` group's authentication (`AuthenticateByToken`/`AuthenticateBySession`) applies. The `Index` handler returns `presenters.ExternalInitiatorResource`, which includes `AccessKey` (the `IncomingAccessKey`, the credential external initiators use to authenticate). `OutgoingToken` is also exposed. The `Secret`/`HashedSecret`/`OutgoingSecret` fields are not in this resource, but `AccessKey` is a legitimate credential value that should not be exposed to arbitrary authenticated users.

### Title
Unrestricted GET /v2/external_initiators discloses all External Initiator AccessKeys to any authenticated (view-role) user - ([File: core/web/router.go], [core/web/external_initiators_controller.go])

### Summary
The route `authv2.GET("/external_initiators", paginatedRequest(eia.Index))` in `core/web/router.go` (line 264) is registered with no role-requirement wrapper, unlike `Create`/`Destroy` on the same resource which are wrapped with `auth.RequiresEditRole`. Any authenticated user — including the lowest "view" role — can call this endpoint and receive every External Initiator's `AccessKey` and `OutgoingToken` in the JSON response.

### Finding Description
`v2Routes` in `core/web/router.go` mounts `authv2` group with authentication (`AuthenticateByToken`/`AuthenticateBySession`) but no minimum role. `ExternalInitiatorsController.Index` (`core/web/external_initiators_controller.go`, lines 50-59) calls `eic.App.BridgeORM().ExternalInitiators(ctx, offset, size)` to fetch *all* external initiators (system-wide, not scoped to a caller/tenant), and maps each into `presenters.NewExternalInitiatorResource` (`core/web/presenters/external_initiators.go`, lines 67-77), which serializes `AccessKey` and `OutgoingToken` directly into the JSON response. Since `POST`/`DELETE` on the same resource explicitly require `auth.RequiresEditRole`, but `GET` does not require even `auth.RequiresViewRole`, this is an inconsistent/missing authorization check: a view-role authenticated session or API token can enumerate `AccessKey` values for every external initiator registered on the node, regardless of which user/config created them. [1](#0-0) [2](#0-1) [3](#0-2) 

### Impact Explanation
`AccessKey` is the credential an external initiator uses to authenticate as itself when calling back into the node (e.g., to trigger job runs via `POST /v2/jobs/:ID/runs`, matched by `auth.AuthenticateExternalInitiator` at `core/web/router.go` lines 450-456). Disclosure of `AccessKey` values to a low-privileged (view-role) user allows that user to impersonate other external initiators if they can also obtain/derive the corresponding `Secret` (used for HMAC signing in `AuthenticateExternalInitiator`). While the `Secret`/`HashedSecret` are not returned by this endpoint, exposing `AccessKey` still weakens the credential model by revealing one half of the two-part secret to unauthorized principals, and constitutes cross-user credential-adjacent information disclosure that violates least-privilege/authorization exactness (view role should not see edit-role-scoped resources' identifiers).

### Likelihood Explanation
Any user with the lowest "view" role and a valid session or API token can call this endpoint with a single unauthenticated-to-node-but-authenticated-as-view HTTP GET — no further preconditions, no rate limiting bypass, and it is fully repeatable (paginated, returns all initiators). This matches the described attacker capability exactly (view/run-role user, no edit/admin).

### Recommendation
Wrap `authv2.GET("/external_initiators", ...)` with at minimum `auth.RequiresViewRole` (if not already the group default) or, more appropriately given the sensitivity of `AccessKey`, require `auth.RequiresEditRole` to match the `Create`/`Destroy` handlers on the same resource. Additionally, consider removing `AccessKey`/`OutgoingToken` from the list-index presenter response entirely (only return them once, at creation time, as is already done via `ExternalInitiatorAuthentication`), since an index/list endpoint has no legitimate need to redisplay credentials.

### Proof of Concept
Go handler-level integration test plan:
1. In `core/web/external_initiators_controller_test.go`, set up a test app via `cltest.NewApplicationWithConfig` and create two users: a "view" role user (`sessions.UserRoleView`) and an "admin"/"edit" user who creates an `ExternalInitiator` via `POST /v2/external_initiators`.
2. Authenticate the view-role user via `client.AuthenticateBySession`/`cltest.NewUserWebAuthSessionClient` to obtain a session/token.
3. Issue `GET /v2/external_initiators` using the view-role user's client.
4. Assert response status is `200 OK` (currently succeeds with no role check) and that the JSON body's `data[].attributes.accessKey` and `outgoingToken` fields are non-empty and match the initiator created by the other user — proving cross-user disclosure.
5. Assert failure of the invariant: expect (post-fix) that this same request returns `401`/`403` unless the caller has at least Edit role, by re-running the test after adding `auth.RequiresEditRole` to the route and confirming the view-role user now receives `http.StatusUnauthorized`.

### Citations

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
