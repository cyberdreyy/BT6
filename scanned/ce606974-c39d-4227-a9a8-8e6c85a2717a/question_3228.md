# Q3228: lending_pool_set_fixed_oracle_price: oracle-config path binds the wrong bank or group [replay-of-a-previously-valid] [identity-vs-shape]

## Question
Can an unprivileged attacker supply replay of a previously valid fixed-price config layout to `lending_pool_set_fixed_oracle_price` so `lending_pool_set_fixed_oracle_price` reconfigures the wrong bank/group oracle context, violating `fixed-price setting must remain strictly role-bound and coherent with downstream cache/state assumptions` and causing `Critical: attacker-forced mispricing of a live bank`? Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/set_fixed_oracle_price.rs` / `lending_pool_set_fixed_oracle_price`
- Entrypoint: `lending_pool_set_fixed_oracle_price`
- Attacker controls: replay of a previously valid fixed-price config layout
- Exploit idea: Probe whether bank/group binding is enforced as tightly as signer authorization on pricing config paths. Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.
- Invariant to test: fixed-price setting must remain strictly role-bound and coherent with downstream cache/state assumptions
- Expected Immunefi impact: Critical: attacker-forced mispricing of a live bank
- Fast validation: Mix same-group and cross-group banks and assert pricing config changes can only land on the exact validated bank. Substitute same-type oracle-related accounts and assert every non-canonical identity is rejected even if shape-compatible.
