# Q3245: lending_pool_set_fixed_oracle_price: price-cache or fixed-price mode switch leaves unsafe mixed state [candidate-banks-from-another-group] [downstream-cache]

## Question
Can an unprivileged attacker make `lending_pool_set_fixed_oracle_price` reach `lending_pool_set_fixed_oracle_price` with candidate banks from another group with the same mint family so a price mode switch leaves unsafe mixed state, violating `fixed-price setting must remain strictly role-bound and coherent with downstream cache/state assumptions` and leading to `Critical: attacker-forced mispricing of a live bank`? Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/set_fixed_oracle_price.rs` / `lending_pool_set_fixed_oracle_price`
- Entrypoint: `lending_pool_set_fixed_oracle_price`
- Attacker controls: candidate banks from another group with the same mint family
- Exploit idea: Check whether switching oracle modes or staked onramp settings fully invalidates or refreshes dependent cached state. Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.
- Invariant to test: fixed-price setting must remain strictly role-bound and coherent with downstream cache/state assumptions
- Expected Immunefi impact: Critical: attacker-forced mispricing of a live bank
- Fast validation: Perform the controlled switch, then immediately run dependent user actions and assert they see a single coherent pricing mode. After the attempted config mutation, immediately run the dependent public cache or user action and assert no inconsistent mode remains.
