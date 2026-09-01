# Q0543: Account id validation vs the stored key - max-length name

## Question
Can an unprivileged attacker insert an id that passes `env::is_valid_account_id` but is compared differently at lookup time than at insertion time, with a name at the account-id length limit so `format!` yields an over-long id, breaking the invariant that `is_whitelisted(x)` is true exactly for the ids that were inserted, and leading to user funds routed into an attacker-parameterised contract the protocol treats as trusted?

## Target
- File/function: `whitelist/src/lib.rs` - `add_staking_pool / add_factory / is_whitelisted / assert_called_by_foundation`
- Entrypoint: `add_staking_pool` is reachable by any whitelisted factory, which any account can drive through `create_staking_pool`
- Attacker controls: which id gets whitelisted, and the string form of the ids involved
- Exploit idea: Insert an id that passes `env::is_valid_account_id` but is compared differently at lookup time than at insertion time, with a name at the account-id length limit so `format!` yields an over-long id.
- Invariant to test: `is_whitelisted(x)` is true exactly for the ids that were inserted.
- Expected Immunefi impact: Critical - user funds routed into an attacker-parameterised contract the protocol treats as trusted.
- Fast validation: Unit test insert/lookup with adversarial strings.
