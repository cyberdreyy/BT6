# Q2418: Insertion return value misread by the factory - account created, init failed

## Question
Can an unprivileged attacker rely on `add_staking_pool` returning `false` for an already-present id while the factory treats the call as a fresh success, in the case where the account is created but its `new` call fails, breaking the invariant that the factory's success signal matches the whitelist's actual state change, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `whitelist/src/lib.rs` - `add_staking_pool / add_factory / is_whitelisted / assert_called_by_foundation`
- Entrypoint: `add_staking_pool` is reachable by any whitelisted factory, which any account can drive through `create_staking_pool`
- Attacker controls: which id gets whitelisted, and the string form of the ids involved
- Exploit idea: Rely on `add_staking_pool` returning `false` for an already-present id while the factory treats the call as a fresh success, in the case where the account is created but its `new` call fails.
- Invariant to test: The factory's success signal matches the whitelist's actual state change.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim a duplicate insertion.
