# Q1467: No refund path at all - account already exists

## Question
Can an unprivileged attacker exploit the absence of any callback in `create`, so a failed deployment silently consumes the attached deposit, targeting a derived account id that already exists and holds a balance, breaking the invariant that a failed deployment refunds the payer, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `multisig-factory/src/lib.rs` - `MultisigFactory::create`
- Entrypoint: `create(name, members, num_confirmations)` - `#[payable]`, callable by ANY account
- Attacker controls: `name`, the full `members` list, `num_confirmations`, the deposit and the prepaid gas
- Exploit idea: Exploit the absence of any callback in `create`, so a failed deployment silently consumes the attached deposit, targeting a derived account id that already exists and holds a balance.
- Invariant to test: A failed deployment refunds the payer.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim a failing deployment and track the deposit.
