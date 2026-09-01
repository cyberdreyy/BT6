# Q4418: Factory set trusted transitively - id derived from a victim account

## Question
Can an unprivileged attacker use a whitelisted factory that itself accepts arbitrary caller input to reach the whitelist, making the trust boundary meaningless, deriving the account id from a victim's account id, breaking the invariant that the whitelist only reflects checks a trusted party actually performed, and leading to user funds routed into an attacker-parameterised contract the protocol treats as trusted?

## Target
- File/function: `whitelist/src/lib.rs` - `add_staking_pool / add_factory / is_whitelisted / assert_called_by_foundation`
- Entrypoint: `add_staking_pool` is reachable by any whitelisted factory, which any account can drive through `create_staking_pool`
- Attacker controls: which id gets whitelisted, and the string form of the ids involved
- Exploit idea: Use a whitelisted factory that itself accepts arbitrary caller input to reach the whitelist, making the trust boundary meaningless, deriving the account id from a victim's account id.
- Invariant to test: The whitelist only reflects checks a trusted party actually performed.
- Expected Immunefi impact: Critical - user funds routed into an attacker-parameterised contract the protocol treats as trusted.
- Fast validation: Trace the trust chain in sim from an unprivileged call.
