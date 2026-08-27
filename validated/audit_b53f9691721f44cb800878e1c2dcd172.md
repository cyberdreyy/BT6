Confirmed: the presenter copies `RelayConfig` verbatim with no redaction.

### Title
OCR2 job presenter echoes raw RelayConfig blob (including embedded secrets) to any authenticated reader - ([File: core/web/presenters/job.go])

### Summary
`NewOffChainReporting2Spec` copies `spec.RelayConfig` (a `map[string]any` decoded from the job's TOML) directly into the presenter's `RelayConfig` field without any filtering or redaction. Since `GET /v2/jobs/:ID` and `GET /v2/jobs` return this presenter as-is, any secret-looking key (e.g. `apiKey`, node URLs with embedded tokens) placed in `relayConfig` by an admin during job creation is echoed verbatim to any user able to read job specs.

### Finding Description
`OffChainReporting2Spec.RelayConfig` is declared as `map[string]any` with `json:"relayConfig"` [1](#0-0) , and `NewOffChainReporting2Spec` assigns `RelayConfig: spec.RelayConfig` straight from `job.OCR2OracleSpec` with no masking, no denylist of sensitive keys, and no structural redaction step [2](#0-1) . Unlike some other spec fields in this file that explicitly convert only non-sensitive values, `RelayConfig` is passed through in its entirety. Whatever an admin encodes in the job TOML's `relayConfig` block (RPC provider URLs with embedded API keys/tokens, chain-specific credential fields, etc.) is stored as-is in `job.OCR2OracleSpec.RelayConfig` and re-serialized unmodified into the JSON response body for `GET /v2/jobs/:ID` and `GET /v2/jobs`. Any authenticated caller permitted to hit these read endpoints — including a view-only role — receives the full unredacted blob.

### Impact Explanation
This matches the "sensitive data exposure / secret disclosure" bounty class: relay/provider credentials (e.g., RPC endpoint tokens) embedded in job configuration by an operator become readable by any authenticated low-privilege user with job-read access, rather than being confined to the creator/admin. If the exposed credential is a third-party RPC API key, an attacker with only read access to the node API can exfiltrate and reuse that key against the provider, incurring cost/DoS/quota abuse against the operator's provider account, or pivot to further reconnaissance.

### Likelihood Explanation
Requires an admin to have placed a secret-bearing value inside `relayConfig` (a realistic and common practice for RPC URLs with query-string API keys), and requires only a "view" role authenticated caller to issue a standard `GET /v2/jobs/:ID` request — no special conditions, race, or timing needed, and fully repeatable for every OCR2 job with such a field.

### Recommendation
Redact known-sensitive keys (e.g., URL query parameters, fields named like `apiKey`, `token`, `password`, `secret`) within `RelayConfig` before presenting it, or omit `RelayConfig` entirely from read responses for non-admin roles, mirroring how other credential-bearing fields (e.g., DB passwords, keystore secrets) are redacted elsewhere in the presenters package.

### Proof of Concept
Go table test in `core/web/presenters/job_test.go`:
1. Construct a `job.OCR2OracleSpec` with `RelayConfig: map[string]any{"chainID": 1, "apiKey": "super-secret-token-123", "nodeURL": "https://rpc.example.com/?key=super-secret-token-123"}`.
2. Call `presenters.NewOffChainReporting2Spec(spec)`.
3. Assert `result.RelayConfig["apiKey"] == "super-secret-token-123"` and `result.RelayConfig["nodeURL"]` contains the token unredacted.
4. Optionally add an HTTP-handler-level integration test hitting `GET /v2/jobs/:ID` with a view-role authenticated session and assert the JSON response body contains the literal secret string, confirming end-to-end exposure through the route.

### Citations

**File:** core/web/presenters/job.go (L170-186)
```go
// OffChainReporting2Spec defines the spec details of a OffChainReporting2 Job
type OffChainReporting2Spec struct {
	ContractID                        string           `json:"contractID"`
	Relay                             string           `json:"relay"` // RelayID.Network
	RelayConfig                       map[string]any   `json:"relayConfig"`
	P2PV2Bootstrappers                pq.StringArray   `json:"p2pv2Bootstrappers"`
	OCRKeyBundleID                    null.String      `json:"ocrKeyBundleID"`
	TransmitterID                     null.String      `json:"transmitterID"`
	ObservationTimeout                sqlutil.Interval `json:"observationTimeout"`
	BlockchainTimeout                 sqlutil.Interval `json:"blockchainTimeout"`
	ContractConfigTrackerPollInterval sqlutil.Interval `json:"contractConfigTrackerPollInterval"`
	ContractConfigConfirmations       uint16           `json:"contractConfigConfirmations"`
	OnchainSigningStrategy            map[string]any   `json:"onchainSigningStrategy"`
	CreatedAt                         time.Time        `json:"createdAt"`
	UpdatedAt                         time.Time        `json:"updatedAt"`
	CollectTelemetry                  bool             `json:"collectTelemetry"`
}
```

**File:** core/web/presenters/job.go (L188-206)
```go
// NewOffChainReporting2Spec initializes a new OffChainReportingSpec from a
// job.OCR2OracleSpec
func NewOffChainReporting2Spec(spec *job.OCR2OracleSpec) *OffChainReporting2Spec {
	return &OffChainReporting2Spec{
		ContractID:                        spec.ContractID,
		Relay:                             spec.Relay,
		RelayConfig:                       spec.RelayConfig,
		P2PV2Bootstrappers:                spec.P2PV2Bootstrappers,
		OCRKeyBundleID:                    spec.OCRKeyBundleID,
		TransmitterID:                     spec.TransmitterID,
		BlockchainTimeout:                 spec.BlockchainTimeout,
		ContractConfigTrackerPollInterval: spec.ContractConfigTrackerPollInterval,
		ContractConfigConfirmations:       spec.ContractConfigConfirmations,
		OnchainSigningStrategy:            spec.OnchainSigningStrategy,
		CreatedAt:                         spec.CreatedAt,
		UpdatedAt:                         spec.UpdatedAt,
		CollectTelemetry:                  spec.CaptureEATelemetry,
	}
}
```
