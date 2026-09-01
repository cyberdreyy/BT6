# Q3188: Access keys added for attacker public keys - callback fails

## Question
Can an unprivileged attacker supply `MultisigMember::AccessKey` entries so `new`'s member loop installs attacker-controlled function-call keys on the new account, when the callback promise itself fails after deployment succeeded, breaking the invariant that the keys installed on a new multisig belong to its intended members, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig-factory/src/lib.rs` - `MultisigFactory::create`
- Entrypoint: `create(name, members, num_confirmations)` - `#[payable]`, callable by ANY account
- Attacker controls: `name`, the full `members` list, `num_confirmations`, the deposit and the prepaid gas
- Exploit idea: Supply `MultisigMember::AccessKey` entries so `new`'s member loop installs attacker-controlled function-call keys on the new account, when the callback promise itself fails after deployment succeeded.
- Invariant to test: The keys installed on a new multisig belong to its intended members.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Deploy and enumerate the account's access keys.
