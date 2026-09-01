# Q1218: Bulk insertion via repeated factory calls - account already exists

## Question
Can an unprivileged attacker repeat factory-driven insertions so the whitelist accumulates attacker-controlled ids that lockups will accept, targeting a derived account id that already exists and holds a balance, breaking the invariant that the whitelist only grows with pools a trusted party vetted, and leading to user funds routed into an attacker-parameterised contract the protocol treats as trusted?

## Target
- File/function: `whitelist/src/lib.rs` - `add_staking_pool / add_factory / is_whitelisted / assert_called_by_foundation`
- Entrypoint: `add_staking_pool` is reachable by any whitelisted factory, which any account can drive through `create_staking_pool`
- Attacker controls: which id gets whitelisted, and the string form of the ids involved
- Exploit idea: Repeat factory-driven insertions so the whitelist accumulates attacker-controlled ids that lockups will accept, targeting a derived account id that already exists and holds a balance.
- Invariant to test: The whitelist only grows with pools a trusted party vetted.
- Expected Immunefi impact: Critical - user funds routed into an attacker-parameterised contract the protocol treats as trusted.
- Fast validation: Sim many insertions and inspect the set.
