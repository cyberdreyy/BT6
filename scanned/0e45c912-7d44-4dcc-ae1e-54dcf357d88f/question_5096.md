# Q5096: Num_confirmations of zero - id derived from a victim account

## Question
Can an unprivileged attacker deploy with `num_confirmations = 0` so a single `confirm` (or none) executes any request, deriving the account id from a victim's account id, breaking the invariant that a deployed multisig always requires at least one confirmation, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig-factory/src/lib.rs` - `MultisigFactory::create`
- Entrypoint: `create(name, members, num_confirmations)` - `#[payable]`, callable by ANY account
- Attacker controls: `name`, the full `members` list, `num_confirmations`, the deposit and the prepaid gas
- Exploit idea: Deploy with `num_confirmations = 0` so a single `confirm` (or none) executes any request, deriving the account id from a victim's account id.
- Invariant to test: A deployed multisig always requires at least one confirmation.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Deploy with zero and execute a transfer request.
