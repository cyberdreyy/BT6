This confirms the vulnerability described in the question.

### Title
Unauthenticated-role/view-role user can read all ExternalInitiator AccessKey/OutgoingToken secrets via unprotected GET /v2/external_initiators - (File: core/web/router.go)

### Summary
The `GET /v2/external_initiators` route is registered as `authv2.GET("/external_initiators", paginatedRequest(eia.Index))` with only session/token authentication and no `auth.RequiresEditRole`/`auth.RequiresRunRole`/`auth.RequiresAdminRole` wrapper, unlike its sibling routes on the same resource. This lets any authenticated user, including one with only `UserRoleView`, list every external initiator's `AccessKey` and `OutgoingToken`.

### Finding Description
In `v2Routes` in `core/web/router.go`, the external-initiators resource routes are: [1](#0-0) 

`POST` and `DELETE` are correctly wrapped with `auth.RequiresEditRole`, but `GET /v2/external_initiators` is only wrapped by `paginatedRequest`, which merely parses pagination params and calls `eia.Index` — it performs no role check. `Index` itself does not check the caller's role either: [2](#0-1) 

The `presenters.NewExternalInitiatorResource` function copies the raw `AccessKey` and `OutgoingToken` fields from the DB model directly into the JSON response with no redaction: [3](#0-2) 

Since the group-level middleware on `authv2` only authenticates via token or session (`auth.Authenticate(... AuthenticateByToken, AuthenticateBySession)`) without any role gate, and `Index` is not itself role-wrapped, any authenticated user — including a `UserRoleView` session — can call this endpoint and receive `AccessKey`/`OutgoingToken` for every registered external initiator in the node.

### Impact Explanation
This exposes External Initiator credentials (`AccessKey`, `OutgoingToken`, indirectly enabling forged `AuthenticateExternalInitiator` requests) to a low-privileged view-only user, allowing that user to impersonate an external initiator and trigger job runs it should not be able to control — a role/authorization-bypass leading to credential disclosure and potential unauthorized job execution, matching the "authorization bypass / secret disclosure" bounty impact class.

### Likelihood Explanation
Minimal precondition: only a valid session cookie for a `UserRoleView` account (obtainable via normal `POST /sessions`) is required — no admin/operator access needed. The request is a single unauthenticated-in-privilege `GET /v2/external_initiators?size=25&page=1` call, fully reproducible and repeatable with no rate-limiting or additional barriers beyond normal session auth.

### Recommendation
Wrap the GET route with the same edit-role requirement as `Create`/`Destroy` (or a dedicated read-role check consistent with sensitive-credential exposure), e.g.:
```go
authv2.GET("/external_initiators", auth.RequiresEditRole(paginatedRequest(eia.Index)))
```
Additionally, consider not returning `AccessKey`/`OutgoingToken` at all in `Index` (list) responses — return them only on `Create`, since `ExternalInitiatorResource` currently echoes these secrets on every list call regardless of role.

### Proof of Concept
Go handler-level integration test (following the pattern in `core/web/external_initiators_controller_test.go`):
1. Set up test app with `cltest.NewApplicationWithKey` and create two sessions: one with `clsessions.UserRoleAdmin` (or edit) to seed an external initiator via `POST /v2/external_initiators` (capturing its `AccessKey`/`OutgoingToken`), and one with `clsessions.UserRoleView`.
2. Using the view-role session's cookie, send `GET /v2/external_initiators?size=25&page=1`.
3. Assert response status is `200 OK` (not `401`/`403`).
4. Decode the JSON:API response into `[]presenters.ExternalInitiatorResource` and assert the returned `AccessKey` and `OutgoingToken` match the values captured at creation time.
5. As a control, repeat step 2 against `POST /v2/external_initiators` or `DELETE /v2/external_initiators/:Name` with the same view-role session and assert `401`/`403` is returned, demonstrating the inconsistency: those routes correctly enforce `RequiresEditRole` while `Index` does not.

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
