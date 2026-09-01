# Q2708: Name squatting a planned multisig id - account created, init failed

## Question
Can an unprivileged attacker create `<name>.<factory>` before the intended owner does, so the deployed multisig has attacker-chosen members at the address others will trust, in the case where the account is created but its `new` call fails, breaking the invariant that a multisig at a given factory sub-account has the members its intended owner chose, and leading to user funds routed into an attacker-parameterised contract the protocol treats as trusted?

## Target
- File/function: `multisig-factory/src/lib.rs` - `MultisigFactory::create`
- Entrypoint: `create(name, members, num_confirmations)` - `#[payable]`, callable by ANY account
- Attacker controls: `name`, the full `members` list, `num_confirmations`, the deposit and the prepaid gas
- Exploit idea: Create `<name>.<factory>` before the intended owner does, so the deployed multisig has attacker-chosen members at the address others will trust, in the case where the account is created but its `new` call fails.
- Invariant to test: A multisig at a given factory sub-account has the members its intended owner chose.
- Expected Immunefi impact: Critical - user funds routed into an attacker-parameterised contract the protocol treats as trusted.
- Fast validation: Sim attacker-first creation and compare member sets.
