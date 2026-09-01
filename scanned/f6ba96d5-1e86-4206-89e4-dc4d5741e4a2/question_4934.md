# Q4934: Empty member list - owner set to the attacker

## Question
Can an unprivileged attacker deploy with an empty `members` list, leaving a funded multisig whose requests nobody can confirm or whose checks degenerate, naming the attacker themselves as owner of the created contract, breaking the invariant that a deployed multisig always has at least `num_confirmations` members, and leading to permanent freezing of user funds?

## Target
- File/function: `multisig-factory/src/lib.rs` - `MultisigFactory::create`
- Entrypoint: `create(name, members, num_confirmations)` - `#[payable]`, callable by ANY account
- Attacker controls: `name`, the full `members` list, `num_confirmations`, the deposit and the prepaid gas
- Exploit idea: Deploy with an empty `members` list, leaving a funded multisig whose requests nobody can confirm or whose checks degenerate, naming the attacker themselves as owner of the created contract.
- Invariant to test: A deployed multisig always has at least `num_confirmations` members.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Deploy empty and attempt a request.
