cache used by `check_block_has_valid_parent` lags behind the true tip by even one processed block. Call sequence: `validate()` -> `check_block_has_valid_parent` reads stale
