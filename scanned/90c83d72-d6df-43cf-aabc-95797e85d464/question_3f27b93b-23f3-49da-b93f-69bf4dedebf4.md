[File: 'external-crates/move/crates/move-bytecode-verifier/src/reference_safety/mod.rs -> Scope: Critical. Irreversible fund lock, frozen withdrawal or redemption path, permanently unclaimable object or coin state, or unrecoverable bridged value in transaction, bridge, staking, accumulator, or settlement flows'] [Symbol: AbstractState::st_loc() / is_local_borrowed()] st_loc() checks is_local_borrowed(local) (any borrow, not just mutable) before overwriting a local with a new value; is_local_borrowed() calls has_consistent_borrows(frame_root, Some(Label::Local(local))); if a prior BorrowLoc created a borrow edge Label::Local(local) on frame_root, st_loc() correctly

```python
questions = [
