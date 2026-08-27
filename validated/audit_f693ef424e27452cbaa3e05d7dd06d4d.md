### Title
`JobsController.Update()` performs delete+recreate without validating that the TOML's identity fields match the target job, allowing `ExternalJobID` to be silently changed via `PUT /v2/jobs/:ID` - (File: core/web/jobs_controller.go)

### Summary
`JobsController.Update()` parses the request TOML into a brand-new `job.Job` struct via `validateJobSpec`, only forces the numeric `jb.ID` to match the URL `:ID`, then deletes the old job and inserts the new one. It never compares the new job's `ExternalJobID` (or other identity fields derivable from the TOML) against the existing job's values before the delete+recreate, so a caller with edit access to the numeric job ID can change the `ExternalJobID` while keeping the same URL-addressable numeric ID.

### Finding Description
The relevant code path is: [1](#0-0) 

`Update()`:
1. Binds `request.TOML` into `UpdateJobRequest` — the TOML content is fully attacker/caller-controlled.
2. Calls `jc.validateJobSpec(ctx, request.TOML)`, which parses the TOML through type-specific validators (e.g. `ocr.ValidatedOracleSpecToml`, `validate.ValidatedOracleSpecToml` for OCR2, etc.) and returns a fresh `job.Job` (`jb`). These validators build the `ExternalJobID` from whatever the TOML specifies (or generate a new one if absent) — the returned `jb` is a completely new job object, not a mutation of the existing job's record.
3. The code then does `err = jb.SetID(c.Param("ID"))` — this only sets the numeric primary-key `ID` field to match the URL, leaving `jb.ExternalJobID` (and any other TOML-derived fields) exactly as parsed from the new TOML.
4. `jc.App.DeleteJob(ctx, jb.ID)` deletes the existing job row (matched only by numeric ID).
5. `jc.App.AddJobV2(ctx, &jb)` inserts the new job, including whatever `ExternalJobID` came from the TOML.

At no point does `Update()` fetch the existing job (e.g., via `FindJob`) and compare `ExternalJobID` or other identity fields between old and new before performing the delete+recreate. There is no check enforcing that the TOML's `externalJobID` (if the format allows specifying one) matches the pre-existing job's identifier. As a result, any caller authorized to hit `PUT /v2/jobs/:ID` can submit a TOML with a different `externalJobID`, and it will be silently accepted, changing the externally-referenced identifier while the URL path segment `:ID` (numeric) stays constant.

### Impact Explanation
Any external system, webhook, or dashboard that keys off `ExternalJobID` (used for run-log correlation, on-chain/off-chain job identity, or webhook endpoint routing, per `job.Job.ExternalJobID`) can be silently redirected to a different, caller-controlled job spec without any warning, since the numeric `:ID` route stays the same but the underlying job's identity changes. This is a data-integrity / identity-confusion issue rather than a direct fund-loss or credential-disclosure primitive — it falls into a "logic error affecting request/response identity" class rather than a critical authentication or privilege-escalation bug.

### Likelihood Explanation
This requires only the same "edit" role/privilege that is already required to call `PUT /v2/jobs/:ID` at all (i.e., no privilege escalation beyond the intended authorization for job management endpoints). The precondition is simply knowledge of a target job's numeric ID, which is enumerable/discoverable via `GET /v2/jobs`. However, this is scoped to a caller who is already authorized to edit jobs — the "attacker" in this scenario is not gaining unauthorized access to someone else's job; they are exploiting the *same* privilege they hold to mutate an identity field with no confirmation step. This significantly reduces severity, since edit-role callers are already trusted to fully replace a job's spec (including its business logic), and changing `ExternalJobID` is a natural consequence of "replace this job with a completely different spec," which is the explicitly documented behavior of `Update()` ("stops and deletes existing job, saves and starts a new job").

### Recommendation
If identity continuity of `ExternalJobID` across updates is a desired invariant, `Update()` should fetch the existing job via `jc.App.JobORM().FindJob(ctx, jb.ID)` before deleting, and either (a) preserve/carry over the existing `ExternalJobID` onto the new spec, ignoring/overriding whatever the TOML specifies, or (b) reject the update with a 422 if TOML explicitly supplies an `externalJobID` that differs from the existing job's value.

### Proof of Concept
Handler-level integration test outline:
1. Create job A via `Create()` with TOML that yields `ExternalJobID = uuid1`.
2. Call `Update()` with `PUT /v2/jobs/:ID` (same numeric ID) but TOML containing/producing `ExternalJobID = uuid2`.
3. `GET /v2/jobs/:ID` and assert whether `ExternalJobID` changed from `uuid1` to `uuid2` with no error/rejection.
4. Expected (per current code): update succeeds, `ExternalJobID` silently changes — demonstrating the missing identity check in `JobsController.Update()` at [2](#0-1) .

Note: I was unable to fully confirm within the available searches whether the TOML format for every job type (e.g., OCR, OCR2, Cron, VRF) actually accepts an explicit `externalJobID` field as user input versus always auto-generating one server-side in the validator — this affects whether an attacker can *choose* an arbitrary `ExternalJobID` value or only cause a new random one to be assigned. Either way, the update path lacks any comparison/guard against the previous job's `ExternalJobID`, but the precise "attacker-chosen value" capability versus "any TOML resubmission random-rotates the ID" nuance requires deeper validator-level inspection than the current search results resolved.

### Citations

**File:** core/web/jobs_controller.go (L170-215)
```go
func (jc *JobsController) Update(c *gin.Context) {
	request := UpdateJobRequest{}
	if err := c.ShouldBindJSON(&request); err != nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, err)
		return
	}

	jb, status, err := jc.validateJobSpec(c.Request.Context(), request.TOML)
	if err != nil {
		jsonAPIError(c, status, err)
		return
	}

	err = jb.SetID(c.Param("ID"))
	if err != nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, err)
		return
	}

	ctx, cancel := context.WithTimeout(c.Request.Context(), 5*time.Second)
	defer cancel()

	// If the provided job id is not matching any job, delete will fail with 404 leaving state unchanged.
	err = jc.App.DeleteJob(ctx, jb.ID)
	// Error can be either come from ORM or from the activeJobs map.
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) || strings.Contains(err.Error(), "job not found") {
			jsonAPIError(c, http.StatusNotFound, errors.Wrap(err, "failed to update job"))
			return
		}
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}

	err = jc.App.AddJobV2(ctx, &jb)
	if err != nil {
		if errors.Is(errors.Cause(err), job.ErrNoSuchKeyBundle) || errors.As(err, &keystore.KeyNotFoundError{}) || errors.Is(errors.Cause(err), job.ErrNoSuchTransmitterKey) || errors.Is(errors.Cause(err), job.ErrNoSuchSendingKey) {
			jsonAPIError(c, http.StatusBadRequest, err)
			return
		}
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}

	jsonAPIResponse(c, presenters.NewJobResource(jb), jb.Type.String())
}
```
