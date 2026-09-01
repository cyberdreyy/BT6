# Q0893: View methods panic on adversarial input - unusable derived id

## Question
Can an unprivileged attacker call `is_whitelisted` or `is_factory_whitelisted` with input that makes the assertion panic inside another contract's callback chain, with a name that makes the derived id an implicit-account or otherwise unusable form, breaking the invariant that view calls never abort a caller's promise chain in a way that strands state, and leading to permanent freezing of user funds?

## Target
- File/function: `whitelist/src/lib.rs` - `add_staking_pool / add_factory / is_whitelisted / assert_called_by_foundation`
- Entrypoint: `add_staking_pool` is reachable by any whitelisted factory, which any account can drive through `create_staking_pool`
- Attacker controls: which id gets whitelisted, and the string form of the ids involved
- Exploit idea: Call `is_whitelisted` or `is_factory_whitelisted` with input that makes the assertion panic inside another contract's callback chain, with a name that makes the derived id an implicit-account or otherwise unusable form.
- Invariant to test: View calls never abort a caller's promise chain in a way that strands state.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Sim a lockup calling the view with such input.
