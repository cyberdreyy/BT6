# Q3196: lending_pool_configure_bank_oracle: oracle-config path reuses stale auxiliary state from a previous mode [replay-of-a-previously-valid] [identity-vs-shape]

## Question
Can an unprivileged attacker route `lending_pool_configure_bank_oracle` through `lending_pool_configure_bank_oracle` with replay of a previously valid config shape under a new signer so the bank reuses stale auxiliary state from a previous oracle mode, violating `oracle configuration changes must require exact authority and bind to the exact bank/oracle lineage intended` and causing `Critical: attacker-installed pricing enabling theft or insolvency`? Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/config_bank_oracle.rs` / `lending_pool_configure_bank_oracle`
- Entrypoint: `lending_pool_configure_bank_oracle`
- Attacker controls: replay of a previously valid config shape under a new signer
- Exploit idea: Mode transitions must not leave old cached assumptions live if a public bug can trigger them. Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.
- Invariant to test: oracle configuration changes must require exact authority and bind to the exact bank/oracle lineage intended
- Expected Immunefi impact: Critical: attacker-installed pricing enabling theft or insolvency
- Fast validation: Switch modes in adversarial sequences and assert downstream pricing always reflects the final mode only. Substitute same-type oracle-related accounts and assert every non-canonical identity is rejected even if shape-compatible.
