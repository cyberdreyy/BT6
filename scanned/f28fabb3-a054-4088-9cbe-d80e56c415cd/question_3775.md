# Q3775: Source address binding can desync from runtime dst_half oversized_tail

## Question
Can an unprivileged maker or filler choose `takingAmount` near half the quote together with the tail is larger than expected and contains extra garbage so that `addressOfEscrowSrc()` predicts one source-clone address while the runtime `Escrow._validateImmutables()` accepts another, letting pre-funded balances land on the wrong address and causing theft or a permanent freeze?

## Target
- File/function: `contracts/BaseEscrowFactory.sol::addressOfEscrowSrc`, `contracts/Escrow.sol::_validateImmutables`
- Entrypoint: `BaseEscrowFactory.addressOfEscrowSrc(...)` -> source clone deployment -> `EscrowSrc` call
- Attacker controls: the immutable fields hashed into the salt, the encoded post-interaction data, and any pre-funding sent to the predicted address
- Exploit idea: Search for a mismatch between off-chain address prediction and runtime immutable validation on source clones.
- Invariant to test: The address computed before source funding must be identical to the only address that later accepts those immutables.
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Predict the source clone for `takingAmount` near half the quote and the tail is larger than expected and contains extra garbage, pre-fund it, deploy through the live fill path, and confirm that runtime immutable validation still binds to the same address.
