# Q1493: Lookup used as an authorisation proof - exact minimum deposit

## Question
Can an unprivileged attacker exploit the fact that consumers treat `is_whitelisted == true` as proof of safety, when the factory only proved account creation, attaching exactly the minimum the method requires, breaking the invariant that consumers' trust assumption matches what the whitelist actually guarantees, and leading to user funds routed into an attacker-parameterised contract the protocol treats as trusted?

## Target
- File/function: `whitelist/src/lib.rs` - `add_staking_pool / add_factory / is_whitelisted / assert_called_by_foundation`
- Entrypoint: `add_staking_pool` is reachable by any whitelisted factory, which any account can drive through `create_staking_pool`
- Attacker controls: which id gets whitelisted, and the string form of the ids involved
- Exploit idea: Exploit the fact that consumers treat `is_whitelisted == true` as proof of safety, when the factory only proved account creation, attaching exactly the minimum the method requires.
- Invariant to test: Consumers' trust assumption matches what the whitelist actually guarantees.
- Expected Immunefi impact: Critical - user funds routed into an attacker-parameterised contract the protocol treats as trusted.
- Fast validation: Trace a lockup's selection path end to end.
