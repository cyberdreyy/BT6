# Q3091: lending_pool_configure_bank_oracle: oracle-config path binds the wrong bank or group [two-banks-whose-oracle-like] [downstream-cache]

## Question
Can an unprivileged attacker supply two banks whose oracle-like accounts are type-compatible to `lending_pool_configure_bank_oracle` so `lending_pool_configure_bank_oracle` reconfigures the wrong bank/group oracle context, violating `oracle configuration changes must require exact authority and bind to the exact bank/oracle lineage intended` and causing `Critical: attacker-installed pricing enabling theft or insolvency`? Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/config_bank_oracle.rs` / `lending_pool_configure_bank_oracle`
- Entrypoint: `lending_pool_configure_bank_oracle`
- Attacker controls: two banks whose oracle-like accounts are type-compatible
- Exploit idea: Probe whether bank/group binding is enforced as tightly as signer authorization on pricing config paths. Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.
- Invariant to test: oracle configuration changes must require exact authority and bind to the exact bank/oracle lineage intended
- Expected Immunefi impact: Critical: attacker-installed pricing enabling theft or insolvency
- Fast validation: Mix same-group and cross-group banks and assert pricing config changes can only land on the exact validated bank. After the attempted config mutation, immediately run the dependent public cache or user action and assert no inconsistent mode remains.
