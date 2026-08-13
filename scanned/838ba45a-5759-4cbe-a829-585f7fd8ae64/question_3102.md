# Q3102: lending_pool_configure_bank_oracle: oracle-config path binds the wrong bank or group [candidate-oracle-related-accounts-from] [identity-vs-shape]

## Question
Can an unprivileged attacker supply candidate oracle-related accounts from sibling bank contexts to `lending_pool_configure_bank_oracle` so `lending_pool_configure_bank_oracle` reconfigures the wrong bank/group oracle context, violating `oracle configuration changes must require exact authority and bind to the exact bank/oracle lineage intended` and causing `Critical: attacker-installed pricing enabling theft or insolvency`? Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/config_bank_oracle.rs` / `lending_pool_configure_bank_oracle`
- Entrypoint: `lending_pool_configure_bank_oracle`
- Attacker controls: candidate oracle-related accounts from sibling bank contexts
- Exploit idea: Probe whether bank/group binding is enforced as tightly as signer authorization on pricing config paths. Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.
- Invariant to test: oracle configuration changes must require exact authority and bind to the exact bank/oracle lineage intended
- Expected Immunefi impact: Critical: attacker-installed pricing enabling theft or insolvency
- Fast validation: Mix same-group and cross-group banks and assert pricing config changes can only land on the exact validated bank. Substitute same-type oracle-related accounts and assert every non-canonical identity is rejected even if shape-compatible.
