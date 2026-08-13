# Q2271: juplend_withdraw: withdraw burns the right derivative but from the wrong owner context [repeated-deposit-withdraw-cycles-to] [recipient-binding]

## Question
Can an unprivileged attacker invoke `juplend_withdraw` with repeated deposit/withdraw cycles to probe asymmetry so `juplend_withdraw` burns the right derivative asset from the wrong owner context, violating `Juplend withdraw must burn the right supply position and release only the value actually redeemed to the correct recipient` and causing `Critical: direct theft or phantom redemption`? Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/withdraw.rs` / `juplend_withdraw`
- Entrypoint: `juplend_withdraw`
- Attacker controls: repeated deposit/withdraw cycles to probe asymmetry
- Exploit idea: Check owner binding for external positions and obligation ownership during redeem paths. Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.
- Invariant to test: Juplend withdraw must burn the right supply position and release only the value actually redeemed to the correct recipient
- Expected Immunefi impact: Critical: direct theft or phantom redemption
- Fast validation: Mix owner/position pairs across users and assert the withdraw path rejects every mismatched owner context. Swap every plausible destination and assert no accepted path releases value to an unvalidated recipient.
