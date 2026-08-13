# Q3313: lending_pool_set_fixed_oracle_price: oracle-config path reuses stale auxiliary state from a previous mode [an-attacker-signer-attempting-to] [downstream-cache]

## Question
Can an unprivileged attacker route `lending_pool_set_fixed_oracle_price` through `lending_pool_set_fixed_oracle_price` with an attacker signer attempting to set a live bank fixed price so the bank reuses stale auxiliary state from a previous oracle mode, violating `fixed-price setting must remain strictly role-bound and coherent with downstream cache/state assumptions` and causing `Critical: attacker-forced mispricing of a live bank`? Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/set_fixed_oracle_price.rs` / `lending_pool_set_fixed_oracle_price`
- Entrypoint: `lending_pool_set_fixed_oracle_price`
- Attacker controls: an attacker signer attempting to set a live bank fixed price
- Exploit idea: Mode transitions must not leave old cached assumptions live if a public bug can trigger them. Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.
- Invariant to test: fixed-price setting must remain strictly role-bound and coherent with downstream cache/state assumptions
- Expected Immunefi impact: Critical: attacker-forced mispricing of a live bank
- Fast validation: Switch modes in adversarial sequences and assert downstream pricing always reflects the final mode only. After the attempted config mutation, immediately run the dependent public cache or user action and assert no inconsistent mode remains.
