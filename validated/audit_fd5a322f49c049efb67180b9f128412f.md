Looking at the `allowPushers` / `revokePusher` delegation path in `CompressedOracleV1`, I need to trace whether a previously-authorized party can retain write authority after a state change that should have cleared it — the direct analog to the NFT approval-not-revoked-on-transfer bug.

Let me examine the key functions: