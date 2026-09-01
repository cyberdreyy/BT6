# Q5763: Threshold met with one live confirmation - num_confirmations = 0

## Question
Can an unprivileged attacker exploit `confirmations.len() as u32 + 1 >= self.num_confirmations` counting stored entries rather than live members, on a multisig deployed through `MultisigFactory::create` with `num_confirmations = 0`, breaking the invariant that the count compared against the threshold contains only current members, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig2/src/lib.rs` - `MultiSigContract::confirm / assert_valid_request / current_member`
- Entrypoint: `confirm(request_id)` - reachable by any predecessor the member set matches, and by the account itself through any of its access keys
- Attacker controls: the request id, the identity `current_member()` derives, and the timing relative to membership changes
- Exploit idea: Exploit `confirmations.len() as u32 + 1 >= self.num_confirmations` counting stored entries rather than live members, on a multisig deployed through `MultisigFactory::create` with `num_confirmations = 0`.
- Invariant to test: The count compared against the threshold contains only current members.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Unit test the count against the member set.
