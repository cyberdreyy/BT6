# Q1490: kamino_withdraw: withdraw burns the right derivative but from the wrong owner context [two-reserve-obligation-contexts-that] [round-trip]

## Question
Can an unprivileged attacker invoke `kamino_withdraw` with two reserve/obligation contexts that are type-compatible but economically different so `kamino_withdraw` burns the right derivative asset from the wrong owner context, violating `Kamino withdraw must release only value actually redeemed from the exact obligation/reserve pair and correct recipient` and causing `Critical: direct theft or phantom redemption`? Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/withdraw.rs` / `kamino_withdraw`
- Entrypoint: `kamino_withdraw`
- Attacker controls: two reserve/obligation contexts that are type-compatible but economically different
- Exploit idea: Check owner binding for external positions and obligation ownership during redeem paths. Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.
- Invariant to test: Kamino withdraw must release only value actually redeemed from the exact obligation/reserve pair and correct recipient
- Expected Immunefi impact: Critical: direct theft or phantom redemption
- Fast validation: Mix owner/position pairs across users and assert the withdraw path rejects every mismatched owner context. Run deposit-then-withdraw and withdraw-then-deposit loops and assert no positive attacker value emerges.
