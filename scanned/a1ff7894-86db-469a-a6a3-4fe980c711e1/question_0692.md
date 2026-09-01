# Q0692: Num_confirmations wider than the members list - max-length name

## Question
Can an unprivileged attacker exploit the `u64` parameter being serialised into a `u32` field, so the value the multisig stores differs from the value requested, with a name at the account-id length limit so `format!` yields an over-long id, breaking the invariant that the stored threshold equals the requested threshold, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig-factory/src/lib.rs` - `MultisigFactory::create`
- Entrypoint: `create(name, members, num_confirmations)` - `#[payable]`, callable by ANY account
- Attacker controls: `name`, the full `members` list, `num_confirmations`, the deposit and the prepaid gas
- Exploit idea: Exploit the `u64` parameter being serialised into a `u32` field, so the value the multisig stores differs from the value requested, with a name at the account-id length limit so `format!` yields an over-long id.
- Invariant to test: The stored threshold equals the requested threshold.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Deploy with a large value and read `get_num_confirmations`.
