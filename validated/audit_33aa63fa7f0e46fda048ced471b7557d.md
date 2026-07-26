Looking at the external bug's root cause: **time-based safety windows do not subtract the duration the protocol was paused**, so after unpause the window appears to have elapsed when it hasn't. I need to find the same pattern in the Sui bridge.

Let me examine the V2 limiter bypass logic and the bridge pause mechanism together.