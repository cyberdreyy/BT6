# Q2517: Factory used as an arbitrary whitelisting oracle - callback fails

## Question
Can an unprivileged attacker drive the whitelisted factory to insert an id of the attacker's choosing, since `add_staking_pool` checks only the predecessor and never the code at the id, when the callback promise itself fails after deployment succeeded, breaking the invariant that a whitelisted id runs the pool code the whitelist implies, and leading to user funds routed into an attacker-parameterised contract the protocol treats as trusted?

## Target
- File/function: `whitelist/src/lib.rs` - `add_staking_pool / add_factory / is_whitelisted / assert_called_by_foundation`
- Entrypoint: `add_staking_pool` is reachable by any whitelisted factory, which any account can drive through `create_staking_pool`
- Attacker controls: which id gets whitelisted, and the string form of the ids involved
- Exploit idea: Drive the whitelisted factory to insert an id of the attacker's choosing, since `add_staking_pool` checks only the predecessor and never the code at the id, when the callback promise itself fails after deployment succeeded.
- Invariant to test: A whitelisted id runs the pool code the whitelist implies.
- Expected Immunefi impact: Critical - user funds routed into an attacker-parameterised contract the protocol treats as trusted.
- Fast validation: Sim factory-driven insertion of an arbitrary id.
