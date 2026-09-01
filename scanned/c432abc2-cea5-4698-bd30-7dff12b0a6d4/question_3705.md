# Q3705: Gas subtraction underflow - collides with tracked set

## Question
Can an unprivileged attacker call with prepaid gas below `CREATE_CALL_GAS` so `env::prepaid_gas() - CREATE_CALL_GAS` underflows or starves `new`, with an id colliding with an entry already in the factory's tracked set, breaking the invariant that an under-funded create leaves no half-initialised multisig, and leading to permanent freezing of user funds?

## Target
- File/function: `multisig-factory/src/lib.rs` - `MultisigFactory::create`
- Entrypoint: `create(name, members, num_confirmations)` - `#[payable]`, callable by ANY account
- Attacker controls: `name`, the full `members` list, `num_confirmations`, the deposit and the prepaid gas
- Exploit idea: Call with prepaid gas below `CREATE_CALL_GAS` so `env::prepaid_gas() - CREATE_CALL_GAS` underflows or starves `new`, with an id colliding with an entry already in the factory's tracked set.
- Invariant to test: An under-funded create leaves no half-initialised multisig.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Call with minimal gas and inspect the account.
