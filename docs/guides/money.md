# Money

**Money** shows what your campaign posts earn over the period you pick: what you were paid, what is pending and what ClipForge estimates the rest will earn, by currency, by day, by account and post by post. Every sum on the page is worked out on this Mac from what you entered and the views ClipForge counted.

Until a campaign post goes up, the page says so and points you to the **Briefs** tab on **Campaigns** to find a campaign.

## Where each number comes from

- **Paid** is the amount you entered when you marked a submission paid on its campaign's page.
- **Estimated** is ClipForge's own estimate for a post that is submitted or approved and not yet paid: its newest view count divided by 1,000 times the campaign's rate, nothing under the campaign's minimum views, capped at the most it pays per clip. A post still to submit has no estimate yet, and a rejected post earns nothing.
- **Pending** is the part of the estimate for posts the marketplace has approved but not yet paid.
- **Views** come from Instagram's insights for posts made through the API, and from the counts you enter for everything else (see Views in the campaigns guide).

None of it is the marketplace's own figure. ClipForge never signs in to a marketplace or reads anything behind its login.

## The period

Pick **7 days** (today and the 6 days before it), **30 days** (the default), **This month** (from the 1st) or **All time** (from your first campaign post). A post belongs to the day it went up, on the posting time zone's clock. Everything on the page follows the period.

## Totals

A line for each currency your campaigns pay in, with paid, pending, estimated, views and posts. Amounts in different currencies are never added together here. **None** means there is nothing estimated or pending in that currency yet; paid shows a zero, such as US$0.00, until you are paid.

## Earnings by day

A bar for each day of the period, or a bar for each week from Monday once the period is longer than 60 days. Each bar is stacked by marketplace, or by account with **Split by**, with what was paid solid and what is estimated faded in the same colour. When more than six earned in the period, the five that earned the most keep their names and the rest share a grey **Others**. A marketplace or account keeps its colour whichever period you pick, the colour its place among the biggest earners of all time gives it. There are six colours, so with more than six over time some share one: the legend under the chart always names which is which, and when two that share a colour are both on the chart, the bigger earner of all time keeps it and the other takes a free one.

Point at a bar, or move to it with Tab, to read its figures: the day, its total and each part. The bars are in US$ so they can stack: each currency is turned into US$ through the rates in **Settings**, **Today**, **Exchange rates to US$** (what one of that currency is worth in US$). A currency with no rate counts as if it were US$, and the chart says so above the bars until you add one. When the bars don't fit the window, the row scrolls sideways and opens at today's end.

## Accounts

A row for every account you post from, the ones with posts in the period first: its platform, handle, first niche tag, posts, views, the median of its posts' 7-day views, estimated, pending and paid (each currency on its own), and its best post, which links to the live post when ClipForge has its link. The **Total** row adds each column, currency by currency.

## Posts

The filters at the top scope everything under them: **From** and **To** (dates within the period; leave one empty for no limit), **Account**, **Platform**, **Campaign**, **Marketplace**, **Status** and **Search posts**, which looks in the hook, the campaign, the marketplace and the handle. When nothing matches, **Clear filters** puts every filter back; the sort stays as it was.

- **Best**: the 5 posts with the most views and the 5 that earned the most (paid, or else estimated, in US$).
- **By platform** and **By niche**: posts, views, estimated and paid in US$ for each, the niche being each account's first niche tag.
- **The sheet**: a row for each post with the day it went up, its account, platform, campaign, hook (a link to the live post when there is one), its views at 2 days, at 7 days and now, its status (with the reason under a rejection), and its estimated and paid amounts in the campaign's currency. Press any column's name to sort by it and again to reverse it; a post without the value goes last either way. The sheet draws the first 500 rows and says so when there are more.

## Export CSV

**Export CSV** saves every post the filters keep, in the order shown, not just the 500 the sheet draws, as a file named `clipforge-money-<today's date>.csv`. Its columns are Posted, Account, Platform, Campaign, Marketplace, Hook, Views at 2 days, Views at 7 days, Views, Status, Reason, Currency, Estimated and Paid, with amounts in each campaign's own currency and its code beside them. It opens straight in Numbers, Excel or Google Sheets. Text that a spreadsheet would run as a formula (starting with `=`, `+`, `-` or `@`) is saved with an apostrophe in front, so it stays plain text.
