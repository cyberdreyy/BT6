# Q0142: Empty member list - front-run

## Question
Can an unprivileged attacker deploy with an empty `members` list, leaving a funded multisig whose requests nobody can confirm or whose checks degenerate, by sending the transaction one block ahead of the legitimate creator, breaking the invariant that a deployed multisig always has at least `num_confirmations` members, and leading to permanent freezing of user funds?

## Target
- File/function: `multisig-factory/src/lib.rs` - `MultisigFactory::create`
- Entrypoint: `create(name, members, num_confirmations)` - `#[payable]`, callable by ANY account
- Attacker controls: `name`, the full `members` list, `num_confirmations`, the deposit and the prepaid gas
- Exploit idea: Deploy with an empty `members` list, leaving a funded multisig whose requests nobody can confirm or whose checks degenerate, by sending the transaction one block ahead of the legitimate creator.
- Invariant to test: A deployed multisig always has at least `num_confirmations` members.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Deploy empty and attempt a request.
