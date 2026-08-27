### Title
External-initiator credential can trigger `RunJobV2` because `AuthenticateExternalInitiator` sets `SessionUserKey`, making `isUser` true without a real user session — ([File: core/web/auth/auth.go])

### Finding Description
`PipelineRunsController.Create` gates the ability to run a job by an integer job ID on `_, isUser := auth.GetAuthenticatedUser(c)` [1](#0-0) . The comment explicitly states "only users are allowed to run jobs using int IDs - EIs not allowed" [2](#0-1) , implying this route is (or is intended to be) reachable through an auth chain that includes `AuthenticateExternalInitiator`.

The root cause is in `AuthenticateExternalInitiator` itself: after validating the EI's access key/secret, it not only sets `SessionExternalInitiatorKey`, it also sets `SessionUserKey` to a synthetic `*clsessions.User{Role: clsessions.UserRoleRun}`: [3](#0-2) 

Because `GetAuthenticatedUser` only checks that a `*clsessions.User` object exists at `SessionUserKey` — it does not distinguish "a real authenticated user from `AuthenticateBySession`/`AuthenticateByToken`" from "a synthetic Run-role stand-in set by `AuthenticateExternalInitiator`": [4](#0-3) 

Consequently, any credential holder that only possesses valid External Initiator access key/secret (no user session, no API token) will pass `AuthenticateExternalInitiator`, get a synthetic `User{Role: UserRoleRun}` placed into `SessionUserKey`, and then `isUser` evaluates to `true` in `Create` — directly contradicting the code's stated intent that "EIs not allowed" for integer job IDs. This is not a header-spoofing bug (no client-controlled header manipulation is needed); it's a legitimate, designed auth-middleware path that inadvertently satisfies the `isUser` check meant to exclude EIs. This lets an EI-only credential holder call `prc.App.RunJobV2(ctx, jobID, nil)` for an arbitrary integer job ID [5](#0-4) .

I could not conclusively verify from the indexed router code whether the specific POST route bound to `prc.Create` currently includes `AuthenticateExternalInitiator` in its middleware chain — the visible `router.go` excerpts only show `prc.Index`/`prc.Show` mounted under the `auth.AuthenticateByToken, auth.AuthenticateBySession`-only `authv2` group [6](#0-5) , and the exact registration line for `prc.Create` was not found within the explored file ranges. If `Create` is only ever mounted behind `authv2` (session/token only, no EI method), this specific reachability path does not apply, and the `isUser`/EI-Role confusion in `auth.go` would be a latent design smell rather than an exploitable bug in the current wiring. Given the explicit "EIs not allowed" comment in `Create`, however, it is reasonable to conclude the handler is intended to be reachable via an EI-inclusive auth chain elsewhere in the router (e.g., a webhook-oriented run route), which is the standard reason this defensive check exists at all.

### Impact Explanation
If the `Create` handler is indeed reachable through an EI-inclusive authentication chain, an attacker holding only an External Initiator's access key/secret (a restricted, non-admin, non-user credential explicitly intended only to trigger webhook-type job runs) could invoke `RunJobV2` for any integer job ID on the node, executing arbitrary configured pipelines (including OCR/VRF-type jobs) outside of their intended trigger mechanism. This matches "unauthorized job run" / role-authorization-bypass impact classes, since job execution normally requires at least a `Run`-role authenticated *user* (session or API token), not merely an EI token.

### Likelihood Explanation
Preconditions: attacker must hold a valid, provisioned External Initiator access key and secret (a low but non-zero credential requirement — this is the minimal credential class named in scope). No user session, no admin/API token, and no header spoofing are required — the bypass works exactly as the auth middleware is designed to set context state. If the vulnerable route is reachable, exploitation is deterministic and repeatable with any valid EI credential.

### Recommendation
- In `AuthenticateExternalInitiator`, do not set `SessionUserKey` to a synthetic `User` object; keep EI identity solely under `SessionExternalInitiatorKey`.
- Change `RequiresRunRole` and any handler relying on "run" permission for EI flows to explicitly check `GetAuthenticatedExternalInitiator` rather than overloading `GetAuthenticatedUser`.
- In `PipelineRunsController.Create`, replace the `isUser` boolean check with an explicit check that the returned user is a genuine session/token-authenticated user (e.g., check a real DB-backed field, or check that `GetAuthenticatedExternalInitiator` is NOT set) rather than relying on the mere presence of a `SessionUserKey` object with `Role: UserRoleRun`.

### Proof of Concept
1. Register an External Initiator via `bridges.NewExternalInitiator` / `CreateExternalInitiator` as done in `core/web/router_test.go`'s `TestTokenAuthRequired_TokenCredentials` [7](#0-6) .
2. Create an integer-ID job (e.g., OCR job) via `AddJobV2`, as done in `setupPipelineRunsControllerTests` [8](#0-7) .
3. Send `POST /v2/jobs/<intJobID>/runs` with only `X-Chainlink-EA-AccessKey` / `X-Chainlink-EA-Secret` headers set (no session cookie, no `X-API-KEY`/`X-API-SECRET`).
4. Assert: if the route includes `AuthenticateExternalInitiator` in its middleware chain, the response is `200 OK` with a `pipelineRun` resource (i.e., `RunJobV2` was invoked) — proving EI-only credentials can trigger runs contrary to the "EIs not allowed" comment.
5. Complementary unit test on `auth` package: call `AuthenticateExternalInitiator` with a valid EI token/record, then call `GetAuthenticatedUser(c)`; assert it currently returns `(non-nil, true)` with `Role == UserRoleRun`, demonstrating the root-cause conflation independent of routing.

### Citations

**File:** core/web/pipeline_runs_controller.go (L109-125)
```go
	_, isUser := auth.GetAuthenticatedUser(c)
	// only users are allowed to run jobs using int IDs - EIs not allowed
	if isUser {
		// Is it an int32? Then process it regardless of type
		var jobID int32
		jobID64, err := strconv.ParseInt(idStr, 10, 32)
		if err == nil {
			jobID = int32(jobID64)
			jobRunID, err := prc.App.RunJobV2(ctx, jobID, nil)
			if err != nil {
				jsonAPIError(c, http.StatusInternalServerError, err)
				return
			}
			respondWithPipelineRun(jobRunID)
			return
		}
	}
```

**File:** core/web/auth/auth.go (L143-150)
```go
	c.Set(SessionExternalInitiatorKey, ei)

	// External initiator endpoints (wrapped with AuthenticateExternalInitiator) inherently assume the role
	// of 'run' (required to trigger job runs)
	c.Set(SessionExternalInitiatorKey, ei)
	c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})

	return nil
```

**File:** core/web/auth/auth.go (L177-187)
```go
// GetAuthenticatedUser extracts the authentication user from the context.
func GetAuthenticatedUser(c *gin.Context) (*clsessions.User, bool) {
	obj, ok := c.Get(SessionUserKey)
	if !ok {
		return nil, false
	}

	user, ok := obj.(*clsessions.User)

	return user, ok
}
```

**File:** core/web/router.go (L392-401)
```go
		authv2.GET("/jobs", paginatedRequest(jc.Index))
		authv2.GET("/jobs/:ID", jc.Show)
		authv2.POST("/jobs", auth.RequiresEditRole(jc.Create))
		authv2.PUT("/jobs/:ID", auth.RequiresEditRole(jc.Update))
		authv2.DELETE("/jobs/:ID", auth.RequiresEditRole(jc.Delete))

		// PipelineRunsController
		authv2.GET("/pipeline/runs", paginatedRequest(prc.Index))
		authv2.GET("/jobs/:ID/runs", paginatedRequest(prc.Index))
		authv2.GET("/jobs/:ID/runs/:runID", prc.Show)
```

**File:** core/web/router_test.go (L57-90)
```go
func TestTokenAuthRequired_TokenCredentials(t *testing.T) {
	t.Parallel()

	ctx := t.Context()
	app := cltest.NewApplicationEVMDisabled(t)
	require.NoError(t, app.Start(ctx))

	router := web.Router(t, app, nil)
	ts := httptest.NewServer(router)
	defer ts.Close()

	eia := auth.NewToken()
	url := cltest.WebURL(t, "http://localhost:8888")
	eir := &bridges.ExternalInitiatorRequest{
		Name: uuid.New().String(),
		URL:  &url,
	}
	ea, err := bridges.NewExternalInitiator(eia, eir)
	require.NoError(t, err)
	err = app.BridgeORM().CreateExternalInitiator(ctx, ea)
	require.NoError(t, err)

	request, err := http.NewRequestWithContext(ctx, http.MethodGet, ts.URL+"/v2/ping/", bytes.NewBufferString("{}"))
	require.NoError(t, err)
	request.Header.Set("Content-Type", web.MediaType)
	request.Header.Set("X-Chainlink-EA-AccessKey", eia.AccessKey)
	request.Header.Set("X-Chainlink-EA-Secret", eia.Secret)

	client := clhttptest.NewTestLocalOnlyHTTPClient()
	resp, err := client.Do(request)
	require.NoError(t, err)

	assert.Equal(t, http.StatusOK, resp.StatusCode)
}
```

**File:** core/web/pipeline_runs_controller_test.go (L185-252)
```go
func setupPipelineRunsControllerTests(t *testing.T) (cltest.HTTPClientCleaner, int32, []int64) {
	t.Helper()
	ctx := t.Context()
	ethClient := cltest.NewEthMocksWithStartupAssertions(t)
	ethClient.On("CallContract", mock.Anything, mock.Anything, mock.Anything).Maybe().Return(nil, nil)
	cfg := configtest.NewGeneralConfig(t, func(c *chainlink.Config, s *chainlink.Secrets) {
		c.OCR.Enabled = new(true)
		c.P2P.V2.Enabled = new(true)
		c.P2P.V2.ListenAddresses = &[]string{fmt.Sprintf("127.0.0.1:%d", freeport.GetOne(t))}
		c.P2P.PeerID = &cltest.DefaultP2PPeerID
		c.EVM[0].NonceAutoSync = new(false)
		c.EVM[0].BalanceMonitor.Enabled = new(false)
	})
	app := cltest.NewApplicationWithConfigAndKey(t, cfg, ethClient, cltest.DefaultP2PKey)
	require.NoError(t, app.Start(ctx))
	require.NoError(t, app.KeyStore.OCR().Add(ctx, cltest.DefaultOCRKey))
	client := app.NewHTTPClient(nil)

	key, _ := cltest.MustInsertRandomKey(t, app.KeyStore.Eth())

	nameAndExternalJobID := uuid.New()
	sp := fmt.Sprintf(`
	type               = "offchainreporting"
	schemaVersion      = 1
	externalJobID       = "%s"
	name               = "%s"
	contractAddress    = "%s"
	evmChainID		   = "%s"
	p2pv2Bootstrappers = ["12D3KooWHfYFQ8hGttAYbMCevQVESEQhzJAqFZokMVtom8bNxwGq@127.0.0.1:5001"]
	keyBundleID        = "%s"
	transmitterAddress = "%s"
	observationSource = """
		// data source 1
		ds1          [type=memo value=<"{\\"USD\\": 1}">];
		ds1_parse    [type=jsonparse path="USD"];
		ds1_multiply [type=multiply times=3];

		ds2          [type=memo value=<"{\\"USD\\": 1}">];
		ds2_parse    [type=jsonparse path="USD"];
		ds2_multiply [type=multiply times=3];

		ds3          [type=fail msg="uh oh"];

		ds1 -> ds1_parse -> ds1_multiply -> answer;
		ds2 -> ds2_parse -> ds2_multiply -> answer;
		ds3 -> answer;

		answer [type=median index=0];
	"""
	`, nameAndExternalJobID, nameAndExternalJobID, testutils.NewAddress().Hex(), cltest.FixtureChainID.String(), cltest.DefaultOCRKeyBundleID, key.Address.Hex())
	var jb job.Job
	err := toml.Unmarshal([]byte(sp), &jb)
	require.NoError(t, err)
	var os job.OCROracleSpec
	err = toml.Unmarshal([]byte(sp), &os)
	require.NoError(t, err)
	jb.OCROracleSpec = &os

	err = app.AddJobV2(t.Context(), &jb)
	require.NoError(t, err)

	firstRunID, err := app.RunJobV2(t.Context(), jb.ID, nil)
	require.NoError(t, err)
	secondRunID, err := app.RunJobV2(t.Context(), jb.ID, nil)
	require.NoError(t, err)

	return client, jb.ID, []int64{firstRunID, secondRunID}
}
```
