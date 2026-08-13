# Q3084: lending_pool_configure_bank_oracle: oracle-config auth bypass installs attacker-chosen pricing [replay-of-a-previously-valid] [identity-vs-shape]

## Question
Can an unprivileged attacker invoke `lending_pool_configure_bank_oracle` with replay of a previously valid config shape under a new signer so `lending_pool_configure_bank_oracle` installs or switches to attacker-chosen pricing, violating `oracle configuration changes must require exact authority and bind to the exact bank/oracle lineage intended` and causing `Critical: attacker-installed pricing enabling theft or insolvency`? Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/config_bank_oracle.rs` / `lending_pool_configure_bank_oracle`
- Entrypoint: `lending_pool_configure_bank_oracle`
- Attacker controls: replay of a previously valid config shape under a new signer
- Exploit idea: Oracle and fixed-price config is fully in scope when a public path can reach it without the intended role. Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.
- Invariant to test: oracle configuration changes must require exact authority and bind to the exact bank/oracle lineage intended
- Expected Immunefi impact: Critical: attacker-installed pricing enabling theft or insolvency
- Fast validation: Attempt attacker-authored pricing reconfiguration and assert no oracle/fixed-price field changes without exact authorized signatures. Substitute same-type oracle-related accounts and assert every non-canonical identity is rejected even if shape-compatible.
