# Q3801: Destination address binding can desync from runtime erc20_dst missing_recipients

## Question
Can an unprivileged destination-escrow creator choose a setup where the destination token is ERC20 and the first 40 bytes for fee recipients are truncated so that `addressOfEscrowDst()` predicts one clone address but the deployed `EscrowDst` accepts a different immutable hash at runtime, leaving the funded address unusable or redirectable?

## Target
- File/function: `contracts/BaseEscrowFactory.sol::addressOfEscrowDst`, `contracts/Escrow.sol::_validateImmutables`
- Entrypoint: `BaseEscrowFactory.addressOfEscrowDst(...)` -> `createDstEscrow(...)` -> `EscrowDst` call
- Attacker controls: the full destination immutable set, the parameters blob, and any off-chain funding assumptions about the predicted address
- Exploit idea: Check whether destination address prediction and runtime immutable validation can diverge under hostile inputs.
- Invariant to test: The destination address computed before funding must be the only address that can ever accept that immutable set.
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Predict and fund a destination clone where the destination token is ERC20 and the first 40 bytes for fee recipients are truncated, deploy it, and verify that subsequent `withdraw()` and `cancel()` calls validate against the same address.
