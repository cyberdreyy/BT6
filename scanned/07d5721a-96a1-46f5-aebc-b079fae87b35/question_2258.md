# Q2258: juplend_withdraw: withdraw burns the right derivative but from the wrong owner context [a-supply-position-and-market] [round-trip]

## Question
Can an unprivileged attacker invoke `juplend_withdraw` with a supply position and market context from different users or markets so `juplend_withdraw` burns the right derivative asset from the wrong owner context, violating `Juplend withdraw must burn the right supply position and release only the value actually redeemed to the correct recipient` and causing `Critical: direct theft or phantom redemption`? Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/withdraw.rs` / `juplend_withdraw`
- Entrypoint: `juplend_withdraw`
- Attacker controls: a supply position and market context from different users or markets
- Exploit idea: Check owner binding for external positions and obligation ownership during redeem paths. Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.
- Invariant to test: Juplend withdraw must burn the right supply position and release only the value actually redeemed to the correct recipient
- Expected Immunefi impact: Critical: direct theft or phantom redemption
- Fast validation: Mix owner/position pairs across users and assert the withdraw path rejects every mismatched owner context. Run deposit-then-withdraw and withdraw-then-deposit loops and assert no positive attacker value emerges.
