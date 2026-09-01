# Q4549: Supply drift across many transfer_calls - legacy bound

## Question
Can an unprivileged attacker loop transfer_calls with partial refunds so rounding or clamping drifts the supply away from the backing, relying on the value `legacy_storage::storage_minimum_balance` reports rather than the current bound, breaking the invariant that supply equals backing after every settled transfer, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/lib.rs` - `impl_fungible_token_core! - ft_transfer / ft_transfer_call / ft_resolve_transfer`
- Entrypoint: the standard NEP-141 methods exposed on wNEAR - any holder, any receiver contract
- Attacker controls: the receiver contract's code and return value, the amounts, and the ordering of the resolve callback
- Exploit idea: Loop transfer_calls with partial refunds so rounding or clamping drifts the supply away from the backing, relying on the value `legacy_storage::storage_minimum_balance` reports rather than the current bound.
- Invariant to test: Supply equals backing after every settled transfer.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Loop in sim and reconcile each iteration.
