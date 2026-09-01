# Q5222: Creation onto an existing funded account - id derived from a victim account

## Question
Can an unprivileged attacker target a name whose derived account already exists and holds NEAR, so the batch fails after the transfer, deriving the account id from a victim's account id, breaking the invariant that a failed creation strands no NEAR, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `multisig-factory/src/lib.rs` - `MultisigFactory::create`
- Entrypoint: `create(name, members, num_confirmations)` - `#[payable]`, callable by ANY account
- Attacker controls: `name`, the full `members` list, `num_confirmations`, the deposit and the prepaid gas
- Exploit idea: Target a name whose derived account already exists and holds NEAR, so the batch fails after the transfer, deriving the account id from a victim's account id.
- Invariant to test: A failed creation strands no NEAR.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim creation onto an existing account.
