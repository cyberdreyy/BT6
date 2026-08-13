# Q3278: lending_pool_set_fixed_oracle_price: staked-oracle transition can be used to brick live collateral paths [candidate-banks-from-another-group] [identity-vs-shape]

## Question
Can an unprivileged attacker invoke `lending_pool_set_fixed_oracle_price` with candidate banks from another group with the same mint family so `lending_pool_set_fixed_oracle_price` performs a staked-oracle transition that bricks or misprices live collateral paths, violating `fixed-price setting must remain strictly role-bound and coherent with downstream cache/state assumptions` and causing `Critical: attacker-forced mispricing of a live bank`? Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/set_fixed_oracle_price.rs` / `lending_pool_set_fixed_oracle_price`
- Entrypoint: `lending_pool_set_fixed_oracle_price`
- Attacker controls: candidate banks from another group with the same mint family
- Exploit idea: This is in scope when caused by a public bypass, not by an admin making a policy choice. Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.
- Invariant to test: fixed-price setting must remain strictly role-bound and coherent with downstream cache/state assumptions
- Expected Immunefi impact: Critical: attacker-forced mispricing of a live bank
- Fast validation: Exercise the transition under attacker-controlled auth/binding attempts and assert it cannot affect live banks without the intended role and full validation. Substitute same-type oracle-related accounts and assert every non-canonical identity is rejected even if shape-compatible.
