# Q3085: lending_pool_configure_bank_oracle: oracle-config auth bypass installs attacker-chosen pricing [candidate-oracle-related-accounts-from] [downstream-cache]

## Question
Can an unprivileged attacker invoke `lending_pool_configure_bank_oracle` with candidate oracle-related accounts from sibling bank contexts so `lending_pool_configure_bank_oracle` installs or switches to attacker-chosen pricing, violating `oracle configuration changes must require exact authority and bind to the exact bank/oracle lineage intended` and causing `Critical: attacker-installed pricing enabling theft or insolvency`? Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/config_bank_oracle.rs` / `lending_pool_configure_bank_oracle`
- Entrypoint: `lending_pool_configure_bank_oracle`
- Attacker controls: candidate oracle-related accounts from sibling bank contexts
- Exploit idea: Oracle and fixed-price config is fully in scope when a public path can reach it without the intended role. Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.
- Invariant to test: oracle configuration changes must require exact authority and bind to the exact bank/oracle lineage intended
- Expected Immunefi impact: Critical: attacker-installed pricing enabling theft or insolvency
- Fast validation: Attempt attacker-authored pricing reconfiguration and assert no oracle/fixed-price field changes without exact authorized signatures. After the attempted config mutation, immediately run the dependent public cache or user action and assert no inconsistent mode remains.
