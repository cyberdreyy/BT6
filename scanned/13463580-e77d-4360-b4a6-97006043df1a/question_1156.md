# Q1156: Get_accounts pagination hides an account - last block of an epoch

## Question
Can an unprivileged attacker exploit the `from_index + limit` bound in `get_accounts` so an account holding a claim never appears to an integrator reconciling the pool, in the last block of an epoch so the state change lands before the reward is distributed, breaking the invariant that enumeration returns every account holding a positive claim, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `staking-pool/src/internal.rs` - `internal_get_account / internal_save_account / get_account`
- Entrypoint: any delegator method, plus the public view methods `get_account`, `get_accounts`, `get_number_of_accounts`
- Attacker controls: when its row exists, when it is deleted, and what an integrator reads from the views
- Exploit idea: Exploit the `from_index + limit` bound in `get_accounts` so an account holding a claim never appears to an integrator reconciling the pool, in the last block of an epoch so the state change lands before the reward is distributed.
- Invariant to test: Enumeration returns every account holding a positive claim.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Compare enumeration output against `get_number_of_accounts`.
