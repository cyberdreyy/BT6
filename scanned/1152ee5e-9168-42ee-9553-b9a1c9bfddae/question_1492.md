# Q1492: Factory deploying a stale embedded wasm - account already exists

## Question
Can an unprivileged attacker rely on the factory's `include_bytes!` copy of the multisig wasm differing from the audited source, so deployed behaviour is not what the members expect, targeting a derived account id that already exists and holds a balance, breaking the invariant that the deployed code matches the audited multisig, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig-factory/src/lib.rs` - `MultisigFactory::create`
- Entrypoint: `create(name, members, num_confirmations)` - `#[payable]`, callable by ANY account
- Attacker controls: `name`, the full `members` list, `num_confirmations`, the deposit and the prepaid gas
- Exploit idea: Rely on the factory's `include_bytes!` copy of the multisig wasm differing from the audited source, so deployed behaviour is not what the members expect, targeting a derived account id that already exists and holds a balance.
- Invariant to test: The deployed code matches the audited multisig.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Compare the embedded bytes against a fresh build.
