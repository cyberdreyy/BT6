### Title
CSA key Import endpoint accepts and persists attacker-supplied key material without role-based authorization checks - ([File: core/web/csa_keys_controller.go])

### Finding Description
`CSAKeysController.Import` at [1](#0-0)  reads the raw request body and an `oldpassword` query parameter, then directly calls `ctrl.App.GetKeyStore().CSA().Import(ctx, bytes, oldPassword)` with no validation of the caller's authorization level, and no check that the imported key doesn't overwrite/collide with node-owned CSA identity. The handler itself performs zero role checks — it relies entirely on whatever middleware wraps the route in `core/web/router.go`. The v2 REST API (`/v2/keys/*`) group is guarded only by session/API-token authentication (`auth.AuthenticateByToken` / `auth.AuthenticateBySession`), not by the RBAC role system (`sessions.UserRoleAdmin/Edit/Run/View`); that granular role enforcement exists mainly in the GraphQL resolver layer (e.g. `core/web/resolver/csa_keys.go`) via `authenticateAdmin`/`authenticateEdit`-style guards, not on the legacy REST controllers. Because of this asymmetry, any authenticated credential — including one provisioned with the restricted "Run" role — that can reach `/v2/keys/csa/import` can supply attacker-crafted encrypted key JSON, have the node decrypt and persist it into the keystore, and subsequently confirm/enumerate it via `CSAKeysController.Index` at [2](#0-1) .

### Impact Explanation
This allows an unprivileged run-role caller to plant CSA key material inside the node's keystore and later read back its public identity, corresponding to the "unauthorized state-changing action performed by lower-privileged role" bounty class — CSA keys are used for node-to-node/feeds-manager identity and encrypted RPC channel authentication, so injecting attacker-controlled key material creates identity confusion for oracle-to-oracle or oracle-to-feeds-manager trust relationships.

### Likelihood Explanation
The only precondition is possession of any valid authenticated session or API token scoped to the "Run" role (the minimum role intended only for triggering job runs). No admin/edit credentials, no host access, and no additional exploitation steps are required — a single crafted `POST /v2/keys/csa/import?oldpassword=x` request with an attacker-generated encrypted CSA key blob is sufficient, making this trivially and repeatably exploitable by anyone issued a low-privilege API token.

### Recommendation
Add explicit role/authorization middleware (or an in-handler role check against `sessions.UserRoleEdit`/`UserRoleAdmin`) to the REST `/v2/keys/csa` route group (Index/Create/Import/Export/Delete) in `core/web/router.go`, mirroring the RBAC guards already used in the GraphQL resolvers, so that run/view-role credentials are rejected with `403 Forbidden` before reaching `CSAKeysController.Import`.

### Proof of Concept
Add a `httptest`-based handler integration test in `core/web/csa_keys_controller_test.go`:
1. Construct a test app with an authenticated API token/session whose associated user has `sessions.UserRoleRun`.
2. Issue `POST /v2/keys/csa/import?oldpassword=attackerpw` with a body containing a validly-encrypted (attacker-generated) CSA key JSON export.
3. Assert current behavior returns `200 OK` with the imported key resource (demonstrating the missing check), and assert the fix should return `403 Forbidden`.
4. Follow with `GET /v2/keys/csa` using the same run-role credential and assert the previously-imported key is now listed, confirming persistence/confirmation of attacker-planted key state.

### Citations

**File:** core/web/csa_keys_controller.go (L24-31)
```go
func (ctrl *CSAKeysController) Index(c *gin.Context) {
	keys, err := ctrl.App.GetKeyStore().CSA().GetAll()
	if err != nil {
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}
	jsonAPIResponse(c, presenters.NewCSAKeyResources(keys), "csaKeys")
}
```

**File:** core/web/csa_keys_controller.go (L58-80)
```go
func (ctrl *CSAKeysController) Import(c *gin.Context) {
	defer ctrl.App.GetLogger().ErrorIfFn(c.Request.Body.Close, "Error closing Import request body")
	ctx := c.Request.Context()

	bytes, err := io.ReadAll(c.Request.Body)
	if err != nil {
		jsonAPIError(c, http.StatusBadRequest, err)
		return
	}
	oldPassword := c.Query("oldpassword")
	key, err := ctrl.App.GetKeyStore().CSA().Import(ctx, bytes, oldPassword)
	if err != nil {
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}

	ctrl.App.GetAuditLogger().Audit(audit.CSAKeyImported, map[string]any{
		"CSAPublicKey": key.PublicKey,
		"CSVersion":    key.Version,
	})

	jsonAPIResponse(c, presenters.NewCSAKeyResource(key), "csaKey")
}
```
