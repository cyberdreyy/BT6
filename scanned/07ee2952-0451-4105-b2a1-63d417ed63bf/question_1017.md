# Q1017: Member list with duplicates - unusable derived id

## Question
Can an unprivileged attacker deploy with a `members` list containing the same principal twice so the effective member count is below the apparent one, with a name that makes the derived id an implicit-account or otherwise unusable form, breaking the invariant that distinct members in the stored set equal the distinct principals requested, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig-factory/src/lib.rs` - `MultisigFactory::create`
- Entrypoint: `create(name, members, num_confirmations)` - `#[payable]`, callable by ANY account
- Attacker controls: `name`, the full `members` list, `num_confirmations`, the deposit and the prepaid gas
- Exploit idea: Deploy with a `members` list containing the same principal twice so the effective member count is below the apparent one, with a name that makes the derived id an implicit-account or otherwise unusable form.
- Invariant to test: Distinct members in the stored set equal the distinct principals requested.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Deploy with duplicates and count members.
