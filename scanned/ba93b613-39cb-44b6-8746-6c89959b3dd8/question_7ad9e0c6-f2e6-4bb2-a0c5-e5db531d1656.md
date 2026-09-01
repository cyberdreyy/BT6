each call independently decrements account.unstaked by 1 and last_total_balance by 1, firing X separate un-awaited Promise::transfer(1) calls with no batching or netting
