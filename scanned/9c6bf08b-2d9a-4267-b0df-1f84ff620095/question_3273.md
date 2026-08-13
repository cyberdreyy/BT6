# Q3273: lending_pool_set_fixed_oracle_price: staked-oracle transition can be used to brick live collateral paths [a-mode-switch-scenario-where] [downstream-cache]

## Question
Can an unprivileged attacker invoke `lending_pool_set_fixed_oracle_price` with a mode-switch scenario where stale cache from a prior oracle mode exists so `lending_pool_set_fixed_oracle_price` performs a staked-oracle transition that bricks or misprices live collateral paths, violating `fixed-price setting must remain strictly role-bound and coherent with downstream cache/state assumptions` and causing `Critical: attacker-forced mispricing of a live bank`? Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/set_fixed_oracle_price.rs` / `lending_pool_set_fixed_oracle_price`
- Entrypoint: `lending_pool_set_fixed_oracle_price`
- Attacker controls: a mode-switch scenario where stale cache from a prior oracle mode exists
- Exploit idea: This is in scope when caused by a public bypass, not by an admin making a policy choice. Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.
- Invariant to test: fixed-price setting must remain strictly role-bound and coherent with downstream cache/state assumptions
- Expected Immunefi impact: Critical: attacker-forced mispricing of a live bank
- Fast validation: Exercise the transition under attacker-controlled auth/binding attempts and assert it cannot affect live banks without the intended role and full validation. After the attempted config mutation, immediately run the dependent public cache or user action and assert no inconsistent mode remains.
