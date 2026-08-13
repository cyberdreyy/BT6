# Q3324: lending_pool_set_fixed_oracle_price: oracle-config path reuses stale auxiliary state from a previous mode [replay-of-a-previously-valid] [identity-vs-shape]

## Question
Can an unprivileged attacker route `lending_pool_set_fixed_oracle_price` through `lending_pool_set_fixed_oracle_price` with replay of a previously valid fixed-price config layout so the bank reuses stale auxiliary state from a previous oracle mode, violating `fixed-price setting must remain strictly role-bound and coherent with downstream cache/state assumptions` and causing `Critical: attacker-forced mispricing of a live bank`? Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/set_fixed_oracle_price.rs` / `lending_pool_set_fixed_oracle_price`
- Entrypoint: `lending_pool_set_fixed_oracle_price`
- Attacker controls: replay of a previously valid fixed-price config layout
- Exploit idea: Mode transitions must not leave old cached assumptions live if a public bug can trigger them. Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.
- Invariant to test: fixed-price setting must remain strictly role-bound and coherent with downstream cache/state assumptions
- Expected Immunefi impact: Critical: attacker-forced mispricing of a live bank
- Fast validation: Switch modes in adversarial sequences and assert downstream pricing always reflects the final mode only. Substitute same-type oracle-related accounts and assert every non-canonical identity is rejected even if shape-compatible.
