# Q0868: Re-adding an id the foundation removed - unusable derived id

## Question
Can an unprivileged attacker re-insert through the factory an id the foundation deliberately removed, with a name that makes the derived id an implicit-account or otherwise unusable form, breaking the invariant that foundation removals cannot be undone by unprivileged callers, and leading to user funds routed into an attacker-parameterised contract the protocol treats as trusted?

## Target
- File/function: `whitelist/src/lib.rs` - `add_staking_pool / add_factory / is_whitelisted / assert_called_by_foundation`
- Entrypoint: `add_staking_pool` is reachable by any whitelisted factory, which any account can drive through `create_staking_pool`
- Attacker controls: which id gets whitelisted, and the string form of the ids involved
- Exploit idea: Re-insert through the factory an id the foundation deliberately removed, with a name that makes the derived id an implicit-account or otherwise unusable form.
- Invariant to test: Foundation removals cannot be undone by unprivileged callers.
- Expected Immunefi impact: Critical - user funds routed into an attacker-parameterised contract the protocol treats as trusted.
- Fast validation: Sim removal then factory re-insertion.
