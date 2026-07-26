I'll analyze the bug class (a secondary minting path that bypasses invariant checks enforced on the primary path) and search for a Sui analog.

Let me look more carefully at the `coin_registry` burn path and the `balance::send_funds` function for potential invariant bypasses.