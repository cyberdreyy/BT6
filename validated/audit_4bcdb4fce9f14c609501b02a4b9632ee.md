## Analysis

The GitLab bug is a case where privileged data/actions (design uploads, which require `Developer`+) leak through a lower-privileged code path (`Move`) that wasn't gated by the same authorization check as the primary path. There's a directly analogous authorization gap in the chainlink node's External Initiator management routes.

### Title
Unprivileged (view-role) authenticated users can read External Initiator `OutgoingToken` secrets via `GET /v2/external_initiators` while creation/deletion require Edit role - (File: `core/web/router.go`)

### Summary
`ExternalInitiatorsController.Create` and `.Destroy` are both explicitly gated behind `auth.RequiresEditRole`, reflecting the intent that managing External Initiators (EIs) is an Edit-or-above privilege. However, the `Index` route that lists EIs and returns their `OutgoingToken` (a secret credential used by the node to authenticate itself when calling back out to the initiator) is registered with no role requirement at all beyond basic session/token authentication.

### Finding Description
In `core/web/router.go`, the EI routes are: [1](#0-0) 

`Create` and `Destroy` require `auth.RequiresEditRole`, but `Index` is wrapped only in `paginatedRequest`, with no role check — meaning any authenticated user, including one with only the lowest `view` role (or a `run`-role External Initiator/API token), can call it.

`Index`'s handler returns full `ExternalInitiatorResource` objects, including `OutgoingToken`: [2](#0-1) [3](#0-2) 

`OutgoingToken`/`OutgoingSecret` are secrets minted at EI creation time specifically so the initiator can authenticate inbound calls that originate from the chainlink node: [4](#0-3) 

The role-check hierarchy elsewhere in the router treats `view` as the minimal, read-only, non-sensitive role — see `RequiresRunRole`/`RequiresEditRole`/`RequiresAdminRole` in `core/web/auth/auth.go`: [5](#0-4) 

Since `Create`/`Destroy` are locked to Edit role (management of EIs is considered a privileged action), but `Index` is reachable by any authenticated principal, a `view`-role user — who by design cannot create or delete anything — can nonetheless read the `OutgoingToken` for every registered External Initiator. This mirrors the GitLab class of bug precisely: a secondary/read path (`Index`, analogous to "Move to") bypasses the authorization gate enforced on the primary/write path (`Create`), exposing privileged data (secrets) to an under-privileged actor.

### Impact Explanation
A `view`-role local user (or an API token scoped as `view`) can retrieve `OutgoingToken` values for all External Initiators configured on the node. This token authenticates outbound requests from the chainlink node to the initiator, so its disclosure to an unprivileged principal breaks the intended privilege boundary that reserves EI management (and, implicitly, its secrets) to Edit/Admin roles.

### Likelihood Explanation
High reachability: this only requires a valid session or API token with the lowest role (`view`) and a single unauthenticated-role-gated `GET /v2/external_initiators` call — no special configuration or race condition needed.

### Recommendation
Wrap `authv2.GET("/external_initiators", ...)` with `auth.RequiresEditRole` (matching `Create`/`Destroy`), or alternatively strip `OutgoingToken`/other secret fields from the `Index` response and only expose them at creation time (as `NewExternalInitiatorAuthentication` already does for `Create`).

### Proof of Concept
1. Create an Admin-level user, then create a second user with `Role: view` (`POST /v2/users`).
2. As Admin, create an External Initiator (`POST /v2/external_initiators`) — note it requires Edit role per `router.go` line 265.
3. Log in / authenticate as the `view`-role user.
4. Call `GET /v2/external_initiators` — this route (router.go line 264) has no role middleware.
5. Observe the response includes `outgoingToken` for the EI created in step 2, despite the requesting user only holding `view` role, which cannot create or destroy EIs. [1](#0-0) [2](#0-1) [6](#0-5)

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

**File:** core/bridges/external_initiator.go (L36-57)
```go
// NewExternalInitiator generates an ExternalInitiator from an
// auth.Token, hashing the password for storage
func NewExternalInitiator(
	eia *auth.Token,
	eir *ExternalInitiatorRequest,
) (*ExternalInitiator, error) {
	salt := utils.NewSecret(utils.DefaultSecretSize)
	hashedSecret, err := auth.HashedSecret(eia, salt)
	if err != nil {
		return nil, pkgerrors.Wrap(err, "error hashing secret for external initiator")
	}

	return &ExternalInitiator{
		Name:           strings.ToLower(eir.Name),
		URL:            eir.URL,
		AccessKey:      eia.AccessKey,
		HashedSecret:   hashedSecret,
		Salt:           salt,
		OutgoingToken:  utils.NewSecret(utils.DefaultSecretSize),
		OutgoingSecret: utils.NewSecret(utils.DefaultSecretSize),
	}, nil
}
```

**File:** core/web/auth/auth.go (L200-236)
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

// RequiresEditRole extracts the user object from the context, and asserts the user's role is at least
// 'edit'
func RequiresEditRole(handler func(*gin.Context)) func(*gin.Context) {
	return func(c *gin.Context) {
		user, ok := GetAuthenticatedUser(c)
		if !ok {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("not a valid session"))
			return
		}
		if user.Role == clsessions.UserRoleView || user.Role == clsessions.UserRoleRun {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("Unauthorized"))
			return
		}
		handler(c)
	}
}
```
