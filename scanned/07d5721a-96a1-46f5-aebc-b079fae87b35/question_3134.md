# Q3134: lending_pool_configure_bank_oracle: oracle-config validation checks shape but not exact key lineage [candidate-oracle-related-accounts-from] [identity-vs-shape]

## Question
Can an unprivileged attacker use `lending_pool_configure_bank_oracle` with candidate oracle-related accounts from sibling bank contexts so `lending_pool_configure_bank_oracle` accepts an oracle-related account with the right shape but wrong lineage, violating `oracle configuration changes must require exact authority and bind to the exact bank/oracle lineage intended` and causing `Critical: attacker-installed pricing enabling theft or insolvency`? Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/config_bank_oracle.rs` / `lending_pool_configure_bank_oracle`
- Entrypoint: `lending_pool_configure_bank_oracle`
- Attacker controls: candidate oracle-related accounts from sibling bank contexts
- Exploit idea: Probe account-key validation where type or owner may be checked but exact configured identity is not. Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.
- Invariant to test: oracle configuration changes must require exact authority and bind to the exact bank/oracle lineage intended
- Expected Immunefi impact: Critical: attacker-installed pricing enabling theft or insolvency
- Fast validation: Substitute same-type oracle-related accounts and assert every non-canonical identity is rejected. Substitute same-type oracle-related accounts and assert every non-canonical identity is rejected even if shape-compatible.
