# Q0917: Name squatting a planned multisig id - unusable derived id

## Question
Can an unprivileged attacker create `<name>.<factory>` before the intended owner does, so the deployed multisig has attacker-chosen members at the address others will trust, with a name that makes the derived id an implicit-account or otherwise unusable form, breaking the invariant that a multisig at a given factory sub-account has the members its intended owner chose, and leading to user funds routed into an attacker-parameterised contract the protocol treats as trusted?

## Target
- File/function: `multisig-factory/src/lib.rs` - `MultisigFactory::create`
- Entrypoint: `create(name, members, num_confirmations)` - `#[payable]`, callable by ANY account
- Attacker controls: `name`, the full `members` list, `num_confirmations`, the deposit and the prepaid gas
- Exploit idea: Create `<name>.<factory>` before the intended owner does, so the deployed multisig has attacker-chosen members at the address others will trust, with a name that makes the derived id an implicit-account or otherwise unusable form.
- Invariant to test: A multisig at a given factory sub-account has the members its intended owner chose.
- Expected Immunefi impact: Critical - user funds routed into an attacker-parameterised contract the protocol treats as trusted.
- Fast validation: Sim attacker-first creation and compare member sets.
