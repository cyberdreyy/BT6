# Q1267: Num_confirmations of zero - account already exists

## Question
Can an unprivileged attacker deploy with `num_confirmations = 0` so a single `confirm` (or none) executes any request, targeting a derived account id that already exists and holds a balance, breaking the invariant that a deployed multisig always requires at least one confirmation, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig-factory/src/lib.rs` - `MultisigFactory::create`
- Entrypoint: `create(name, members, num_confirmations)` - `#[payable]`, callable by ANY account
- Attacker controls: `name`, the full `members` list, `num_confirmations`, the deposit and the prepaid gas
- Exploit idea: Deploy with `num_confirmations = 0` so a single `confirm` (or none) executes any request, targeting a derived account id that already exists and holds a balance.
- Invariant to test: A deployed multisig always requires at least one confirmation.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Deploy with zero and execute a transfer request.
