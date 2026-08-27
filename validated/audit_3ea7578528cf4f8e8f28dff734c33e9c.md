### No Vulnerability found for this question.

The `ReplayFromBlock` handler correctly requires only `run` role via `auth.RequiresRunRole`, matching the operation's intent (triggering replay only) [1](#0-0) . `auth.RequiresRunRole` merely rejects `UserRoleView`, allowing `run`, `edit`, and `admin` — this is consistent with the declared minimum role and does not grant elevated privileges beyond what's needed [2](#0-1) .

Tracing the handler itself, `ReplayController.ReplayFromBlock` only validates/parses the block number, force flag, chain family, and chain ID from the request, then calls `bdc.App.ReplayFromBlock(ctx, chainFamily, chainID, uint64(blockNumber), force)` and returns a response — it performs no other database writes or job/chain-config persistence [3](#0-2) .

The underlying `ChainlinkApplication.ReplayFromBlock` implementation only triggers `LogBroadcaster().ReplayFromBlock(fromBlock, forceBroadcast)` and `LogPoller().ReplayAsync(fromBlock)` for EVM chains, or `relayer.Replay(ctx, ...)` for other chain families — these are in-memory/async replay triggers, not calls into chain-management code paths that mutate persisted job/chain configuration (e.g., no calls to chain config create/update/delete controllers or repositories) [4](#0-3) . There is no code path here that writes to job or chain config tables, so a run-role user cannot achieve edit/admin-equivalent state mutation through this endpoint.

### Citations

**File:** core/web/router.go (L297-298)
```go
		rc := ReplayController{app}
		authv2.POST("/replay_from_block/:number", auth.RequiresRunRole(rc.ReplayFromBlock))
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

**File:** core/web/replay_controller.go (L22-72)
```go
func (bdc *ReplayController) ReplayFromBlock(c *gin.Context) {
	if c.Param("number") == "" {
		jsonAPIError(c, http.StatusUnprocessableEntity, errors.New("missing 'number' parameter"))
		return
	}

	// check if "force" query string parameter provided
	var force bool
	var err error
	if fb := c.Query("force"); fb != "" {
		force, err = strconv.ParseBool(fb)
		if err != nil {
			jsonAPIError(c, http.StatusUnprocessableEntity, errors.Wrap(err, "boolean value required for 'force' query string param"))
			return
		}
	}

	blockNumber, err := strconv.ParseInt(c.Param("number"), 10, 0)
	if err != nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, err)
		return
	}
	if blockNumber < 0 {
		jsonAPIError(c, http.StatusUnprocessableEntity, errors.Errorf("block number cannot be negative: %v", blockNumber))
		return
	}

	chainFamily := c.Query("family")
	if chainFamily == "" {
		jsonAPIError(c, http.StatusUnprocessableEntity, errors.New("chain family was not provided"))
		return
	}

	chainID := c.Query("ChainID")
	if strings.TrimSpace(chainID) == "" {
		jsonAPIError(c, http.StatusUnprocessableEntity, errors.New("chain-id was not provided"))
		return
	}

	ctx := c.Request.Context()
	if err := bdc.App.ReplayFromBlock(ctx, chainFamily, chainID, uint64(blockNumber), force); err != nil {
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}

	response := ReplayResponse{
		Message: "Replay started",
		ChainID: chainID,
	}
	jsonAPIResponse(c, &response, "response")
}
```

**File:** core/services/chainlink/application.go (L1203-1231)
```go
// ReplayFromBlock implements the Application interface.
func (app *ChainlinkApplication) ReplayFromBlock(ctx context.Context, chainFamily string, chainID string, number uint64, forceBroadcast bool) error {
	if chainFamily == relay.NetworkEVM {
		// TODO: Implement EVM Replay on Relayer instead of using LegacyChains - BCFR-1160
		chain, err := app.GetRelayers().LegacyEVMChains().Get(chainID)
		if err != nil {
			return err
		}
		//nolint:gosec // this won't overflow
		fromBlock := int64(number)

		if legacyChain, ok := chain.(legacyevm.Chain); ok {
			legacyChain.LogBroadcaster().ReplayFromBlock(fromBlock, forceBroadcast)
			if app.Config.Feature().LogPoller() {
				legacyChain.LogPoller().ReplayAsync(fromBlock)
			}
			return nil
		}
		// else LOOPP mode, so fall back to default
	}
	relayer, err := app.GetRelayers().Get(commontypes.RelayID{
		Network: chainFamily,
		ChainID: chainID,
	})
	if err != nil {
		return err
	}
	return relayer.Replay(ctx, strconv.FormatUint(number, 10), map[string]any{})
}
```
