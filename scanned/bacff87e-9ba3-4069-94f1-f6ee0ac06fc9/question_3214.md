# Q3214: lending_pool_set_fixed_oracle_price: oracle-config auth bypass installs attacker-chosen pricing [candidate-banks-from-another-group] [identity-vs-shape]

## Question
Can an unprivileged attacker invoke `lending_pool_set_fixed_oracle_price` with candidate banks from another group with the same mint family so `lending_pool_set_fixed_oracle_price` installs or switches to attacker-chosen pricing, violating `fixed-price setting must remain strictly role-bound and coherent with downstream cache/state assumptions` and causing `Critical: attacker-forced mispricing of a live bank`? Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/set_fixed_oracle_price.rs` / `lending_pool_set_fixed_oracle_price`
- Entrypoint: `lending_pool_set_fixed_oracle_price`
- Attacker controls: candidate banks from another group with the same mint family
- Exploit idea: Oracle and fixed-price config is fully in scope when a public path can reach it without the intended role. Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.
- Invariant to test: fixed-price setting must remain strictly role-bound and coherent with downstream cache/state assumptions
- Expected Immunefi impact: Critical: attacker-forced mispricing of a live bank
- Fast validation: Attempt attacker-authored pricing reconfiguration and assert no oracle/fixed-price field changes without exact authorized signatures. Substitute same-type oracle-related accounts and assert every non-canonical identity is rejected even if shape-compatible.
