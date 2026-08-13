# Q3248: lending_pool_set_fixed_oracle_price: price-cache or fixed-price mode switch leaves unsafe mixed state [a-fixed-price-attempt-combined] [identity-vs-shape]

## Question
Can an unprivileged attacker make `lending_pool_set_fixed_oracle_price` reach `lending_pool_set_fixed_oracle_price` with a fixed-price attempt combined with staked onramp mode transitions so a price mode switch leaves unsafe mixed state, violating `fixed-price setting must remain strictly role-bound and coherent with downstream cache/state assumptions` and leading to `Critical: attacker-forced mispricing of a live bank`? Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/set_fixed_oracle_price.rs` / `lending_pool_set_fixed_oracle_price`
- Entrypoint: `lending_pool_set_fixed_oracle_price`
- Attacker controls: a fixed-price attempt combined with staked onramp mode transitions
- Exploit idea: Check whether switching oracle modes or staked onramp settings fully invalidates or refreshes dependent cached state. Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.
- Invariant to test: fixed-price setting must remain strictly role-bound and coherent with downstream cache/state assumptions
- Expected Immunefi impact: Critical: attacker-forced mispricing of a live bank
- Fast validation: Perform the controlled switch, then immediately run dependent user actions and assert they see a single coherent pricing mode. Substitute same-type oracle-related accounts and assert every non-canonical identity is rejected even if shape-compatible.
