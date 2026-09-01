# Q4502: Whitelist storage key collision - id derived from a victim account

## Question
Can an unprivileged attacker insert ids that collide in the underlying `UnorderedSet` key space, deriving the account id from a victim's account id, breaking the invariant that distinct ids occupy distinct entries, and leading to user funds routed into an attacker-parameterised contract the protocol treats as trusted?

## Target
- File/function: `whitelist/src/lib.rs` - `add_staking_pool / add_factory / is_whitelisted / assert_called_by_foundation`
- Entrypoint: `add_staking_pool` is reachable by any whitelisted factory, which any account can drive through `create_staking_pool`
- Attacker controls: which id gets whitelisted, and the string form of the ids involved
- Exploit idea: Insert ids that collide in the underlying `UnorderedSet` key space, deriving the account id from a victim's account id.
- Invariant to test: Distinct ids occupy distinct entries.
- Expected Immunefi impact: Critical - user funds routed into an attacker-parameterised contract the protocol treats as trusted.
- Fast validation: Unit test the set with adversarial ids.
