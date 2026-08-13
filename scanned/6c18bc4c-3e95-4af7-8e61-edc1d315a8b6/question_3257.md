# Q3257: lending_pool_set_fixed_oracle_price: oracle-config validation checks shape but not exact key lineage [a-mode-switch-scenario-where] [downstream-cache]

## Question
Can an unprivileged attacker use `lending_pool_set_fixed_oracle_price` with a mode-switch scenario where stale cache from a prior oracle mode exists so `lending_pool_set_fixed_oracle_price` accepts an oracle-related account with the right shape but wrong lineage, violating `fixed-price setting must remain strictly role-bound and coherent with downstream cache/state assumptions` and causing `Critical: attacker-forced mispricing of a live bank`? Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/set_fixed_oracle_price.rs` / `lending_pool_set_fixed_oracle_price`
- Entrypoint: `lending_pool_set_fixed_oracle_price`
- Attacker controls: a mode-switch scenario where stale cache from a prior oracle mode exists
- Exploit idea: Probe account-key validation where type or owner may be checked but exact configured identity is not. Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.
- Invariant to test: fixed-price setting must remain strictly role-bound and coherent with downstream cache/state assumptions
- Expected Immunefi impact: Critical: attacker-forced mispricing of a live bank
- Fast validation: Substitute same-type oracle-related accounts and assert every non-canonical identity is rejected. After the attempted config mutation, immediately run the dependent public cache or user action and assert no inconsistent mode remains.
