# Clipping channels

A clipping channel is a niche with its own YouTube sources and its own accounts: "Podcasts" might watch three podcast channels and fill two TikTok and two Instagram accounts, while "Gaming" watches streamers for another set. Once a day each clipping channel looks for new uploads, clips them and leaves the best clips on **Today** as drafts. Nothing from a clipping channel posts until you approve it there and press Confirm.

## Make one

Open **Channels** and press **New clipping channel**.

- **Name**: what you call the niche.
- **YouTube channels**: one link a line. A channel link (`youtube.com/@handle`, `/channel/UC...` or `/c/...`) or a link to any one of its videos both work.
- **Also clip the latest**: None by default. See the starting point, below.
- **Accounts**: tick the TikTok and Instagram accounts its drafts are for. Link accounts on **Accounts** first; any mix works, and an account can take drafts from several clipping channels.
- **Clips per video**, **Layout**, **Caption style** and **Focus** (what to look for in each video, if anything): the options each new project gets, as if you had started it by hand.
- **Hashtags**: up to 10, first on every draft's caption.
- **Rights**: see the last section.
- **Check time**, **New videos a day**, **Drafts a day** and **At Claude's usage limit**: see the daily check, below.

Press **Save**. The card shows each YouTube channel with its newest upload, the accounts it drafts for, what the last check found and when the next one runs. **Add source** adds another channel later, and the cross beside a source stops watching it; the drafts and projects it already made stay.

## The starting point

When you add a source, ClipForge looks the channel up once and remembers its newest upload. Only videos published after that are clipped, so adding a channel with years of uploads never floods your drafts. To start with some of what is already there, set **Also clip the latest** to 1 to 5 videos.

## The daily check

At its **Check time** (6:00 am unless you change it, in the time zone set in Settings, Posting, which starts as Sydney) a clipping channel reads each source's public YouTube feed, which lists its 15 latest uploads, and picks out the ones it hasn't seen. **Check now** on the card runs one straight away.

ClipForge has to be running for a check to happen. Opened from ClipForge.app it keeps running after you close its window, until you log out or restart the Mac; the background service ([setup](setup.md#4-the-background-service)) starts it again each time you log in. A check whose time passed while the Mac was asleep or off runs once when ClipForge next starts or wakes, and a clipping channel never checks twice for the same day. While a check's downloads and AI passes run, the Mac is kept from idling to sleep; closing the lid still sleeps it, and the work carries on at wake.

**Pause watching** stops the checks until **Resume watching**. **History** shows the last 30 checks: every video each one saw, what became of it, and why any was skipped.

## What gets skipped

Each skip is in History with its reason.

- Shorts.
- A live stream or premiere that hasn't finished. Each check looks again, for up to 3 days.
- Videos under 4 minutes or over 4 hours. Change both in **Settings**, **Clipping channels**; they hold for every clipping channel.
- Members only, private, age restricted or region blocked videos.

**New videos a day** (3 by default) caps how many a clipping channel clips in a day, **Check now** included. When more pass than that, the newest are clipped and the rest wait for the next day, newest first, and drop off after 3 days.

## Clipping

Each new video becomes an ordinary project with the clipping channel's options, so you can open it from History and work on it like any other. These projects queue behind anything you started by hand. When two clipping channels pick up the same video, or you already started it by hand, the later project reuses the earlier one's downloads, audio, transcript and signals, so only the AI passes run again. If both are working on it at once, the later one waits for those files instead of fetching them a second time.

If Claude's usage limit is reached, **At Claude's usage limit** decides what happens: **Wait for the reset** (the default, so drafts come later but at full quality), **Economy models** or **Basic mode**.

## Drafts and approval

When a check's projects are done, the clipping channel takes the best clips across them that pass QA, as many as its accounts can post in a day under their caps (or **Drafts a day**, if you set it lower). It renders them, whatever **Render on their own** says in Settings, Rendering, and plans each one as the Post dialog would: spread across the accounts, a smart time each, captions and hashtags per account. Those plans are saved as drafts.

On **Today**, under Clips to review, the drafts are grouped by clipping channel and then by source video, each with its clip, account, caption, time, score and warnings, all ticked at first.

1. Untick the weak ones, or press **Discard** on any you don't want (Undo brings it back; its clip stays in the project).
2. Press **Approve and post**. The Post dialog's review step opens on exactly the ticked drafts, moves any time that has passed to the next free one and shows the usual warnings.
3. Press **Confirm**. They become scheduled posts in the Queue and go out at their times, through the same TikTok and Instagram routes as any other post.

Drafts never post, expire or delete on their own. If you leave them for a few days they pile up, and approving them all spreads them over the following days under the caps. Deleting a clipping channel discards its drafts; its projects and the posts you approved stay.

## YouTube manners

A check makes one feed request per source, a few seconds apart, with no login, no API key and never your browser's cookies. **Check now** opens at most once every 10 minutes per clipping channel, and the card says when it opens next. If YouTube asks this Mac to slow down, the clipping channel stops for the day and says so rather than pushing on, and **Check now** stays shut until the next day's check. If a feed stops working, the check falls back to one plain listing of the channel's videos, with the same skips.

## Rights

Clipping other people's videos every day is the fastest way to collect copyright claims, so each clipping channel records what you have:

- **Not cleared yet** (the default): the review step warns that posting these clips may draw a copyright claim. It never blocks; it is there so you decide with your eyes open.
- **I have permission**: the creator said yes.
- **A clipping programme**: the creator runs a programme that pays or allows clippers.

Whatever you pick, the credit line naming the source is on by default, and nothing posts without your approval.
