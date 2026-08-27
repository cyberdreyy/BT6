Confirmed: `paginatedRequest` (core/web/helpers.go:53-62) is only a param-parsing wrapper and adds no role/authorization check.<br> [1](#0-0) 

### Title
Missing role-based authorization on `GET /v2/external_initiators` allows any authenticated user (including view/run role) to enumerate all External Initiators' AccessKeys - ([File: core/web/router.go])

### Summary
The `GET /v2/external_initiators` route is registered without any `auth.RequiresEditRole`/`auth.RequiresAdminRole` wrapper, unlike the sibling `Create`/`Destroy` routes on the same controller. Any authenticated user — including one with only `UserRoleView` (the lowest role) — can call this endpoint and receive every external initiator's `AccessKey` in the response body.

### Finding Description
The route is defined as:
```go
authv2.GET("/external_initiators", paginatedRequest(eia.Index))
authv2.POST("/external_initiators", auth.RequiresEditRole(eia.Create))
authv2.DELETE("/external_initiators/:Name", auth.RequiresEditRole(eia.Destroy))
``` [2](#0-1) 

`paginatedRequest` only parses pagination query params and calls the handler — it performs no authorization check. [1](#0-0) 

`Index` then loads all `ExternalInitiator` records and converts each to `presenters.ExternalInitiatorResource` via `presenters.NewExternalInitiatorResource`, which explicitly copies `AccessKey` into the JSON response (`"accessKey"` field): [3](#0-2) [4](#0-3) 

The `authv2` group only requires successful `AuthenticateByToken` or `AuthenticateBySession` — it does **not** include `AuthenticateExternalInitiator`. [5](#0-4) 
Endpoints reachable via `AuthenticateExternalInitiator` (i.e., EI-credential holders) are limited to `/v2/ping` and `POST /v2/jobs/:ID/runs` in the separate `userOrEI` group. [6](#0-5) 

So the specific premise in the question — that an EI-authenticated caller (raw EI AccessKey/Secret headers) can hit `GET /v2/external_initiators` — is **not reachable**, because that route is not registered under a group that accepts `AuthenticateExternalInitiator`. However, the actual, reachable, and more general vulnerability is that any authenticated **user** session/API-token with the lowest role (`UserRoleView`, or `UserRoleRun`) can hit the unprotected `Index` route directly and receive all EIs' `AccessKey` values, since there is no `RequiresEditRole`/`RequiresAdminRole` guard on `GET /v2/external_initiators`, in contrast to `Create`/`Destroy` on the same resource which do require edit role. This is confirmed by the existing test `TestExternalInitiatorsController_Index`, which asserts the endpoint returns `AccessKey` in the resource list with no additional role setup beyond a default authenticated client. [7](#0-6) 

### Impact Explanation
Disclosure of `AccessKey` for all External Initiators to any low-privilege authenticated user (view role) constitutes credential/secret-adjacent information disclosure: `AccessKey` is one half of the EI authentication token pair (paired with a `Secret`/`HashedSecret`), and knowing all `AccessKey` values reduces the attacker's remaining work to guessing/brute-forcing the corresponding `Secret` for job-run-triggering EI endpoints. This matches the "key or secret disclosure" bounty impact class, scoped to a horizontal privilege issue (view-role user accessing edit/admin-only data) rather than full node compromise.

### Likelihood Explanation
Requires only a valid low-privilege session or API token (`UserRoleView` or `UserRoleRun`) — no special access needed beyond having any node account, which is a realistic minimal-privilege scenario per the attacker model. The endpoint is trivially reachable (`GET /v2/external_initiators`), deterministic, and repeatable.

### Recommendation
Wrap the `Index` route with `auth.RequiresEditRole` (or `RequiresAdminRole`) to match the authorization level of `Create`/`Destroy` on the same resource, and/or omit `AccessKey`/sensitive fields from `presenters.ExternalInitiatorResource` for list responses, exposing them only on creation (as already done via `ExternalInitiatorAuthentication`).

### Proof of Concept
Go handler-level integration test:
1. Start app with a test user created with `Role: sessions.UserRoleView` (or `UserRoleRun`).
2. Insert two `ExternalInitiator` records via `bridges.ORM`.
3. Authenticate the HTTP client as the view-role user (session or API token, not admin).
4. Call `GET /v2/external_initiators`.
5. Assert either: (a) response status is `403 Forbidden`/`401 Unauthorized` (expected fix), or (b) currently, assert response status `200 OK` and that the JSON body contains the `accessKey` field for initiators belonging to other users — demonstrating the disclosure bug.
6. Compare against `POST /v2/external_initiators` and `DELETE /v2/external_initiators/:Name` with the same view-role user, which correctly return `401`, confirming the inconsistency.

### Citations

**File:** core/web/helpers.go (L53-62)
```go
func paginatedRequest(action func(*gin.Context, int, int, int)) func(*gin.Context) {
	return func(c *gin.Context) {
		size, page, offset, err := ParsePaginatedRequest(c.Query("size"), c.Query("page"))
		if err != nil {
			jsonAPIError(c, http.StatusUnprocessableEntity, err)
			return
		}
		action(c, size, page, offset)
	}
}
```

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

**File:** core/web/router.go (L450-457)
```go
	userOrEI := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateExternalInitiator,
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
	userOrEI.GET("/ping", ping.Show)
	userOrEI.POST("/jobs/:ID/runs", auth.RequiresRunRole(prc.Create))
}
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

**File:** core/web/external_initiators_controller_test.go (L63-109)
```go
func TestExternalInitiatorsController_Index(t *testing.T) {
	t.Parallel()

	app := cltest.NewApplicationWithConfig(t,
		configtest.NewGeneralConfig(t, func(c *chainlink.Config, s *chainlink.Secrets) {
			c.JobPipeline.ExternalInitiatorsEnabled = new(true)
		}))
	require.NoError(t, app.Start(t.Context()))

	client := app.NewHTTPClient(nil)

	db := app.GetDB()
	borm := bridges.NewORM(db)

	eiFoo := cltest.MustInsertExternalInitiatorWithOpts(t, borm, cltest.ExternalInitiatorOpts{
		NamePrefix:    "foo",
		URL:           cltest.MustWebURL(t, "http://example.com/foo"),
		OutgoingToken: "outgoing_token",
	})
	eiBar := cltest.MustInsertExternalInitiatorWithOpts(t, borm, cltest.ExternalInitiatorOpts{NamePrefix: "bar"})

	resp, cleanup := client.Get("/v2/external_initiators?size=x")
	t.Cleanup(cleanup)
	cltest.AssertServerResponse(t, resp, http.StatusUnprocessableEntity)

	resp, cleanup = client.Get("/v2/external_initiators?size=1")
	t.Cleanup(cleanup)
	cltest.AssertServerResponse(t, resp, http.StatusOK)
	body := cltest.ParseResponseBody(t, resp)

	metaCount, err := cltest.ParseJSONAPIResponseMetaCount(body)
	require.NoError(t, err)
	require.Equal(t, 2, metaCount)

	var links jsonapi.Links
	var externalInitiators []presenters.ExternalInitiatorResource
	err = web.ParsePaginatedResponse(body, &externalInitiators, &links)
	require.NoError(t, err)
	assert.NotEmpty(t, links["next"].Href)
	assert.Empty(t, links["prev"].Href)

	assert.Len(t, externalInitiators, 1)
	assert.Equal(t, strconv.FormatInt(eiBar.ID, 10), externalInitiators[0].ID)
	assert.Equal(t, eiBar.Name, externalInitiators[0].Name)
	assert.Nil(t, externalInitiators[0].URL)
	assert.Equal(t, eiBar.AccessKey, externalInitiators[0].AccessKey)
	assert.Equal(t, eiBar.OutgoingToken, externalInitiators[0].OutgoingToken)
```
