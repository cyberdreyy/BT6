# Q1493: kamino_withdraw: withdraw burns the right derivative but from the wrong owner context [same-slot-deposit-then-withdraw] [recipient-binding]

## Question
Can an unprivileged attacker invoke `kamino_withdraw` with same-slot deposit then withdraw around the same external position so `kamino_withdraw` burns the right derivative asset from the wrong owner context, violating `Kamino withdraw must release only value actually redeemed from the exact obligation/reserve pair and correct recipient` and causing `Critical: direct theft or phantom redemption`? Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/withdraw.rs` / `kamino_withdraw`
- Entrypoint: `kamino_withdraw`
- Attacker controls: same-slot deposit then withdraw around the same external position
- Exploit idea: Check owner binding for external positions and obligation ownership during redeem paths. Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.
- Invariant to test: Kamino withdraw must release only value actually redeemed from the exact obligation/reserve pair and correct recipient
- Expected Immunefi impact: Critical: direct theft or phantom redemption
- Fast validation: Mix owner/position pairs across users and assert the withdraw path rejects every mismatched owner context. Swap every plausible destination and assert no accepted path releases value to an unvalidated recipient.
