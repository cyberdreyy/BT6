# Q2455: Dst publicWithdraw can freeze on short parameters len_63 fees_exact

## Question
Can an unprivileged destination-escrow creator deploy a funded `EscrowDst` with a 63-byte `parameters` blob while fees sum exactly to `immutables.amount`, then wait for `DstPublicWithdrawal` so that even the public path reverts on fee decoding and the maker payout remains stuck until cancellation or rescue?

## Target
- File/function: `contracts/EscrowDst.sol::publicWithdraw`, `contracts/libraries/ImmutablesLib.sol`
- Entrypoint: `EscrowDst.publicWithdraw(bytes32,IBaseEscrow.Immutables)`
- Attacker controls: the `parameters` length, fee encoding, and the destination-escrow creation transaction
- Exploit idea: Use malformed fee metadata to brick the public withdrawal path as well as the private path.
- Invariant to test: If a destination escrow was funded and the secret is known, the public withdrawal path should still be able to finalize.
- Expected Immunefi impact: Temporary freezing of funds
- Fast validation: Create a destination escrow with a 63-byte `parameters` blob, warp to `DstPublicWithdrawal`, and call `publicWithdraw()` with a valid secret to see whether the fee-decoding revert still blocks settlement.
