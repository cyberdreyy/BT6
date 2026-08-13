# Q3225: lending_pool_set_fixed_oracle_price: oracle-config path binds the wrong bank or group [a-mode-switch-scenario-where] [downstream-cache]

## Question
Can an unprivileged attacker supply a mode-switch scenario where stale cache from a prior oracle mode exists to `lending_pool_set_fixed_oracle_price` so `lending_pool_set_fixed_oracle_price` reconfigures the wrong bank/group oracle context, violating `fixed-price setting must remain strictly role-bound and coherent with downstream cache/state assumptions` and causing `Critical: attacker-forced mispricing of a live bank`? Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/set_fixed_oracle_price.rs` / `lending_pool_set_fixed_oracle_price`
- Entrypoint: `lending_pool_set_fixed_oracle_price`
- Attacker controls: a mode-switch scenario where stale cache from a prior oracle mode exists
- Exploit idea: Probe whether bank/group binding is enforced as tightly as signer authorization on pricing config paths. Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.
- Invariant to test: fixed-price setting must remain strictly role-bound and coherent with downstream cache/state assumptions
- Expected Immunefi impact: Critical: attacker-forced mispricing of a live bank
- Fast validation: Mix same-group and cross-group banks and assert pricing config changes can only land on the exact validated bank. After the attempted config mutation, immediately run the dependent public cache or user action and assert no inconsistent mode remains.
