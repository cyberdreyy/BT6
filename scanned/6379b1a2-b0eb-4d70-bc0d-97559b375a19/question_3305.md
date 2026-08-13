# Q3305: lending_pool_set_fixed_oracle_price: oracle-config path can retarget future permissionless cache writes [a-mode-switch-scenario-where] [downstream-cache]

## Question
Can an unprivileged attacker use `lending_pool_set_fixed_oracle_price` with a mode-switch scenario where stale cache from a prior oracle mode exists so `lending_pool_set_fixed_oracle_price` retargets future permissionless cache writes to attacker-selected pricing context, violating `fixed-price setting must remain strictly role-bound and coherent with downstream cache/state assumptions` and leading to `Critical: attacker-forced mispricing of a live bank`? Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/set_fixed_oracle_price.rs` / `lending_pool_set_fixed_oracle_price`
- Entrypoint: `lending_pool_set_fixed_oracle_price`
- Attacker controls: a mode-switch scenario where stale cache from a prior oracle mode exists
- Exploit idea: Check whether protected config fields later consumed by permissionless cache-refresh paths can be corrupted by a public auth/binding bug. Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.
- Invariant to test: fixed-price setting must remain strictly role-bound and coherent with downstream cache/state assumptions
- Expected Immunefi impact: Critical: attacker-forced mispricing of a live bank
- Fast validation: Mutate the config under attacker conditions, then try the permissionless refresher and assert it still cannot write from attacker-selected sources. After the attempted config mutation, immediately run the dependent public cache or user action and assert no inconsistent mode remains.
