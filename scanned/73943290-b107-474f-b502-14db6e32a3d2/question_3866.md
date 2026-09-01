# Q3866: Unvalidated name yields an unexpected account path - victim named as owner

## Question
Can an unprivileged attacker pass a `name` containing dots or other characters so `format!("{}.{}", name, current_account_id)` produces an id the caller did not intend, naming a victim account as the owner of the created contract, breaking the invariant that the created account is exactly one level under the factory, and leading to user funds routed into an attacker-parameterised contract the protocol treats as trusted?

## Target
- File/function: `multisig-factory/src/lib.rs` - `MultisigFactory::create`
- Entrypoint: `create(name, members, num_confirmations)` - `#[payable]`, callable by ANY account
- Attacker controls: `name`, the full `members` list, `num_confirmations`, the deposit and the prepaid gas
- Exploit idea: Pass a `name` containing dots or other characters so `format!("{}.{}", name, current_account_id)` produces an id the caller did not intend, naming a victim account as the owner of the created contract.
- Invariant to test: The created account is exactly one level under the factory.
- Expected Immunefi impact: Critical - user funds routed into an attacker-parameterised contract the protocol treats as trusted.
- Fast validation: Unit test adversarial names against the derivation.
