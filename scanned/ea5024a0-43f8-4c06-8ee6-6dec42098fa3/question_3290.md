# Q3290: lending_pool_set_fixed_oracle_price: price-setting path updates configuration but not its dependent invariants [a-mode-switch-scenario-where] [identity-vs-shape]

## Question
Can an unprivileged attacker make `lending_pool_set_fixed_oracle_price` reach `lending_pool_set_fixed_oracle_price` with a mode-switch scenario where stale cache from a prior oracle mode exists so protected price config updates without updating dependent invariants, violating `fixed-price setting must remain strictly role-bound and coherent with downstream cache/state assumptions` and causing `Critical: attacker-forced mispricing of a live bank`? Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/set_fixed_oracle_price.rs` / `lending_pool_set_fixed_oracle_price`
- Entrypoint: `lending_pool_set_fixed_oracle_price`
- Attacker controls: a mode-switch scenario where stale cache from a prior oracle mode exists
- Exploit idea: Audit whether mode changes also maintain required cache, limit, or operational-state invariants. Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.
- Invariant to test: fixed-price setting must remain strictly role-bound and coherent with downstream cache/state assumptions
- Expected Immunefi impact: Critical: attacker-forced mispricing of a live bank
- Fast validation: After the controlled config mutation, run all dependent invariants and assert no user path becomes inconsistently permitted. Substitute same-type oracle-related accounts and assert every non-canonical identity is rejected even if shape-compatible.
