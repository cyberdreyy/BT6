# Q3211: lending_pool_set_fixed_oracle_price: oracle-config auth bypass installs attacker-chosen pricing [replay-of-a-previously-valid] [downstream-cache]

## Question
Can an unprivileged attacker invoke `lending_pool_set_fixed_oracle_price` with replay of a previously valid fixed-price config layout so `lending_pool_set_fixed_oracle_price` installs or switches to attacker-chosen pricing, violating `fixed-price setting must remain strictly role-bound and coherent with downstream cache/state assumptions` and causing `Critical: attacker-forced mispricing of a live bank`? Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/set_fixed_oracle_price.rs` / `lending_pool_set_fixed_oracle_price`
- Entrypoint: `lending_pool_set_fixed_oracle_price`
- Attacker controls: replay of a previously valid fixed-price config layout
- Exploit idea: Oracle and fixed-price config is fully in scope when a public path can reach it without the intended role. Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.
- Invariant to test: fixed-price setting must remain strictly role-bound and coherent with downstream cache/state assumptions
- Expected Immunefi impact: Critical: attacker-forced mispricing of a live bank
- Fast validation: Attempt attacker-authored pricing reconfiguration and assert no oracle/fixed-price field changes without exact authorized signatures. After the attempted config mutation, immediately run the dependent public cache or user action and assert no inconsistent mode remains.
