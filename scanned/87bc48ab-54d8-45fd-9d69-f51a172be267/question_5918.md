# Q5918: Deploy or transfer to an arbitrary receiver - account holding a large balance

## Question
Can an unprivileged attacker get a request executed whose `receiver_id` is an attacker account with a `Transfer` or `DeployContract` action, on a multisig account holding a large NEAR balance, breaking the invariant that value and code actions target only what the confirmers approved, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig/src/lib.rs` - `MultiSigContract::add_request / confirm / execute_request (key-based v1)`
- Entrypoint: `add_request` / `confirm` require `predecessor == current_account_id`, i.e. any access key on the multisig account
- Attacker controls: which key signs, the request contents, and the timing around key changes
- Exploit idea: Get a request executed whose `receiver_id` is an attacker account with a `Transfer` or `DeployContract` action, on a multisig account holding a large NEAR balance.
- Invariant to test: Value and code actions target only what the confirmers approved.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Sim the request and trace the NEAR.
