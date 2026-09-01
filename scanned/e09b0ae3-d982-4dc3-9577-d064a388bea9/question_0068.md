# Q0068: Whitelist entry survives the account being re-created - front-run

## Question
Can an unprivileged attacker get an id whitelisted, then have that account deleted and re-created with different code, keeping the whitelist entry, by sending the transaction one block ahead of the legitimate creator, breaking the invariant that a whitelist entry is invalidated when the account behind it changes, and leading to user funds routed into an attacker-parameterised contract the protocol treats as trusted?

## Target
- File/function: `whitelist/src/lib.rs` - `add_staking_pool / add_factory / is_whitelisted / assert_called_by_foundation`
- Entrypoint: `add_staking_pool` is reachable by any whitelisted factory, which any account can drive through `create_staking_pool`
- Attacker controls: which id gets whitelisted, and the string form of the ids involved
- Exploit idea: Get an id whitelisted, then have that account deleted and re-created with different code, keeping the whitelist entry, by sending the transaction one block ahead of the legitimate creator.
- Invariant to test: A whitelist entry is invalidated when the account behind it changes.
- Expected Immunefi impact: Critical - user funds routed into an attacker-parameterised contract the protocol treats as trusted.
- Fast validation: Sim deletion and re-creation of a whitelisted id.
