# Paid campaigns

A paid clipping campaign (on Whop Content Rewards, Vyro or anywhere else) pays per view for clips of the footage it names, as long as each clip keeps its rules. ClipForge keeps each campaign's rules and links, makes the clips of any clipping channel or project you link to it keep those rules, and follows every post from made to paid. Nothing posts without your approval, as everywhere else in ClipForge.

## What ClipForge never does

ClipForge never signs in to a marketplace or payout service, never joins, submits to or automates their sites, never reads anything behind their logins and never keeps payment details. Joining a campaign, submitting a post and withdrawing are things you do yourself on the marketplace's own site. ClipForge keeps the links and opens them in your default browser, never in its own window or an account's posting browser. Every earnings figure it shows is its own estimate from what you entered, not the marketplace's numbers.

## Make a campaign

Open **Campaigns** and press **New campaign**.

1. Paste the campaign's brief or rules from its page into **Campaign rules** and press **Read the rules**. Claude reads the text as plain text (it can't act on anything in it and its answer is checked against the form) and fills in what it can. Anything it can't place goes into **Notes**. Nothing is fetched and nothing is saved yet.
2. Each field Claude filled says **Check this** until you look at it and change it or leave it. Read them against the campaign's page.
3. Tick **I've checked the filled fields** and press **Save**.

You can also skip the paste and fill the form by hand. **Edit** on a campaign's page opens the same form later.

The fields:

- **The campaign**: its name, the marketplace (Whop, Vyro or any name you like), and links to its campaign page and its submit page.
- **Pay**: the currency (USD unless you change it), the rate per 1,000 views, the minimum views a clip needs to earn anything, the most it pays per clip, the budget left (with the date you entered it) and the end date.
- **Platforms and footage**: TikTok, Instagram or both, and where clips may come from: YouTube channels, particular YouTube videos, or files you add. A Google Drive link counts as a file you add, so a brief that hands out its videos on Drive needs that box ticked.
- **Captions**: the hashtags, mentions and text every caption needs.
- **Rules**: what clips can't use, picked from what ClipForge controls (gameplay split, music, sound effects, emoji, hook title, fact cards, progress bar, punch-ins and the on-screen credit line, whose credit then moves into the caption); the shortest and longest clip; and **Rules you check by hand**, one a line, for anything ClipForge can't see for itself, like "no reaction content" or a watermark in the footage.

## Link a clipping channel or a project

- **A clipping channel**: open its **Edit** dialog and pick the campaign under **Campaign**. One campaign can serve several clipping channels; each clipping channel serves one campaign at most.
- **A project you started by hand**: pick the campaign beside the rights badge at the top of the project's page. If the campaign doesn't allow footage from that video's channel, the picker says so and goes back to what it was.

A campaign you marked ended isn't offered, unless it is the one already linked. One that ended by its end date or budget is still offered for now, though its clipping channels make no drafts. Pick **None** to unlink.

Once linked:

- Its rights are **A clipping programme**. Unlinking sets them back to **Not cleared yet**.
- A clipping channel watches only the channels the campaign allows, and clips the campaign's named videos as if they were new uploads, inside its daily video limit. Anything else is skipped, and History names the rule.
- Each clip renders by the rules: the elements it can't use are off, and its length stays inside the limits, which win over an account's Creator Rewards switch.
- Each caption gets the required hashtags, mentions and text.
- Drafts go only to its accounts on the campaign's platforms, with the content disclosure switch on.

## The checklist on each draft

On **Today**, a campaign's draft says which campaign it is for and lists every rule:

- A rule ClipForge checks (length, platform, footage, hashtags, mentions, caption text and the elements it controls) shows a tick when the post keeps it, or what to change when it doesn't, such as editing the caption or rendering the clip again.
- A rule you check by hand is a tick box. Watch the clip, then tick it; it saves straight away. A tick stays with its rule's words, so if you edit a hand rule on the campaign, its box starts unticked again.

No rule passes silently. A draft can't be approved until every rule is kept. If you would rather post it anyway as an ordinary clip, press **Unlink from campaign**: its rules and its submission go, and **Undo** in the message that appears brings them back.

A campaign's clip you post yourself from **Post** gets the same list in the dialog's **Review** step, with the same tick boxes, and **Confirm** waits until every rule is kept.

The checks go on after approval. A Queue edit that takes out a required hashtag, mention or line of text is refused with what to put back. A clip rendered again after approval is checked against the length limits and banned elements before it goes; if the new render breaks one, the post waits for you in the Queue and says what to change. Each post keeps the campaign it was approved for, and its submission is for that campaign, even if its project is linked to another one later.

## When a campaign ends

A campaign ends when its end date passes, when you set its budget left to 0, or when you press **Mark ended** on its page (**Mark active** undoes that). Its clipping channels stop making drafts and say why on their cards, its drafts not yet approved get a warning, and its submissions stay on its page. **Delete** on its page removes a campaign with no submissions yet; its clipping channels and projects stay, unlinked. A campaign with submissions can't be deleted, so its records stay; mark it ended instead.

## Submissions

Every live post of a campaign's clip gets a submission on the campaign's page, including one you exported and put up yourself once you press **Mark as posted** on it in the Queue. Submitting is up to you: **Open submit page** opens the campaign's submit page in your browser, you submit the post there, then mark it here.

A submission moves from **To submit** to **Submitted**, then **Approved** and **Paid** (with the amount you were paid), or **Rejected** (with the reason). A move made by mistake has a button that takes it back. **Show** filters the list by where each one stands.

## Views

- **Instagram posts made through the API**: ClipForge reads each one's views from Instagram's insights once a day, while its campaign runs and for 30 days at most. The account's login needs the `instagram_manage_insights` permission (see the Instagram guide); without it, its posts' views are entered by hand instead.
- **TikTok posts, and Instagram posts made another way**: a count is due by hand when a post is 2 days old, 7 days old and on its campaign's end date. The submission says when one is due; press **Enter views** and type the count from the post.

## Estimates

Each post's estimate is nothing under the campaign's minimum views, and otherwise its views divided by 1,000 times the rate, capped at the most paid per clip, in the campaign's currency. The newest count is the one used.

The Campaigns sheet shows each campaign's views, estimated (submitted and approved posts not yet paid), pending (approved, not yet paid) and paid, with a totals line per currency, never added across currencies. The campaign's page shows the same for that campaign. All of it is ClipForge's estimate from your entries.

## The Campaigns sheet

The **Sheet** tab on **Campaigns** has a row for each campaign. Above it:

- **Search campaigns** looks in each campaign's name, marketplace and linked clipping channels.
- **Marketplace** shows one marketplace's campaigns, **State** shows the active ones, the ones ending within 3 days or the ended ones, and **Needs me** keeps the campaigns with something on today's to-do list.
- The totals lines add up only the rows the search and filters keep, still one line for each currency.
- **Columns** hides or shows any column but the campaign's name and the triangle. What you pick is remembered in this browser on this Mac, and the totals lines lose a hidden column too.

Press a column's name to sort by it. Amounts sort by what they are worth in US$, through the rates in **Settings**, **Today**, **Exchange rates to US$**; a currency with no rate counts as if it were US$. When nothing matches, **Clear filters** brings every campaign back. For what each post earned, day by day and account by account, open **Money** (see the Money guide).

## Marketplaces

**Settings**, **Marketplaces** has a row for each marketplace a campaign names. Give each one its **Withdraw link** (its payout page, which ClipForge opens in your browser) and its **Minimum payout**: a marketplace's payout counts as ready to withdraw once its pending amount reaches the minimum, or once there is any pending amount if you set none. Names match whatever their case, so "Whop" and "whop" share a row.
