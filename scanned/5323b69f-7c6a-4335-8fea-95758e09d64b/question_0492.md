# Q0492: Deployed without a deposit - crafted name

## Question
Can an unprivileged attacker create with zero attached deposit, leaving a contract that cannot pay for its own storage while still holding member keys, with a `name` / `staking_pool_id` argument whose characters change the derived account path, breaking the invariant that a created multisig can pay for its storage, and leading to permanent freezing of user funds?

## Target
- File/function: `multisig-factory/src/lib.rs` - `MultisigFactory::create`
- Entrypoint: `create(name, members, num_confirmations)` - `#[payable]`, callable by ANY account
- Attacker controls: `name`, the full `members` list, `num_confirmations`, the deposit and the prepaid gas
- Exploit idea: Create with zero attached deposit, leaving a contract that cannot pay for its own storage while still holding member keys, with a `name` / `staking_pool_id` argument whose characters change the derived account path.
- Invariant to test: A created multisig can pay for its storage.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Create with no deposit and attempt a request.
