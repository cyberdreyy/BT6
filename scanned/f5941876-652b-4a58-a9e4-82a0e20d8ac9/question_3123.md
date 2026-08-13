# Q3123: lending_pool_configure_bank_oracle: oracle-config validation checks shape but not exact key lineage [two-banks-whose-oracle-like] [downstream-cache]

## Question
Can an unprivileged attacker use `lending_pool_configure_bank_oracle` with two banks whose oracle-like accounts are type-compatible so `lending_pool_configure_bank_oracle` accepts an oracle-related account with the right shape but wrong lineage, violating `oracle configuration changes must require exact authority and bind to the exact bank/oracle lineage intended` and causing `Critical: attacker-installed pricing enabling theft or insolvency`? Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/config_bank_oracle.rs` / `lending_pool_configure_bank_oracle`
- Entrypoint: `lending_pool_configure_bank_oracle`
- Attacker controls: two banks whose oracle-like accounts are type-compatible
- Exploit idea: Probe account-key validation where type or owner may be checked but exact configured identity is not. Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.
- Invariant to test: oracle configuration changes must require exact authority and bind to the exact bank/oracle lineage intended
- Expected Immunefi impact: Critical: attacker-installed pricing enabling theft or insolvency
- Fast validation: Substitute same-type oracle-related accounts and assert every non-canonical identity is rejected. After the attempted config mutation, immediately run the dependent public cache or user action and assert no inconsistent mode remains.
