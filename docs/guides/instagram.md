# Posting to Instagram

ClipForge posts Reels through Meta's official Instagram API. There are two ways in, and each Meta app you make uses one of them:

- **Instagram through Facebook** (recommended): one token links every Instagram account you manage, the video uploads straight from this Mac, and the keys don't expire on a timer. Each Instagram account needs a free Facebook Page.
- **Instagram with Instagram login**: no Facebook Page, but one token per account, refreshed every few weeks, and Instagram has to fetch each video from a public address in front of this Mac.

A third way, **Instagram in Chrome**, posts through instagram.com in a Chrome window like TikTok does. It is experimental, because Instagram changes its pages often; use it only for an account the API can't take.

Whichever way you link, ClipForge never sees a password. Logins happen on Meta's own pages, the keys go into the macOS Keychain (never the database, the logs or the window), and nothing posts until you press Confirm.

## First, the accounts

Every Instagram account must be **public** and a **Creator or Business** account. In the Instagram app: Settings, Account type and tools, Switch to professional account. Both kinds are free.

Your Meta app stays in Development mode. An app that only posts to accounts you manage needs no App Review and no business verification.

## Instagram through Facebook

**Once, for the app:**

1. At developers.facebook.com, create an app of the **Business** type and add the Instagram use case **API setup with Facebook login**.
2. Link each Instagram account to a Facebook Page that your Facebook account can manage with Content or Full control. One Page per Instagram account; on Facebook it is in the Page's settings, under Linked accounts.

**Each time you link accounts:**

1. Open the **Graph API Explorer**, pick your app, and add these permissions: `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement`, `business_management` and `instagram_manage_insights`.
2. Press **Generate Access Token**. In Meta's consent screen, tick every Page and every Instagram account you want ClipForge to post to.
3. Copy the token into the **Access Token Debugger** and press **Extend Access Token**. Copy the long one.
4. In ClipForge: **Accounts**, **Add account**, **Instagram through Facebook**. Paste the token and press **Find accounts**.
5. Tick the accounts to add and press **Add 6 accounts** (it counts what you ticked). Each account's Page key goes into the Keychain and the token you pasted is dropped.

Instead of step 3 you can paste the short token with the app's **App ID** and **App secret**. ClipForge swaps it for a long one itself and never keeps the secret.

**Later.** Meta can end a grant under its 90 day data access rule. The card then shows **Reconnect**; do the steps above again and paste the new token. Linking an account that is already there just gives it the new key.

## Instagram with Instagram login

**Once, for the app:**

1. Make a second Meta app (Meta allows one login type per app) with the Instagram use case **API setup with Instagram login**.
2. In the app's dashboard, add each account as an **Instagram Tester**. Then, logged in to that account, accept the invite at instagram.com/accounts/manage_access.
3. Set up the public address (below) before the first post.

**Each account:**

1. Press **Generate token** beside the account in the app's dashboard. The token lasts 60 days.
2. In ClipForge: **Accounts**, **Add account**, **Instagram with Instagram login**. Paste the token.

ClipForge refreshes each token once it is a week old, so it keeps going as long as ClipForge runs now and then. If refreshing keeps failing, the card warns 10 days before the token runs out, and **Reconnect** takes a new one.

### The public address

In this mode Instagram won't take the video as an upload: it fetches it from an https address you give it. ClipForge serves each video at its own random address only while that post is uploading, removes it afterwards, and answers nothing else from the public side.

Tailscale Funnel can provide that address. With Tailscale on and Funnel allowed for your tailnet, run this once in Terminal:

```sh
tailscale funnel --bg --set-path /m http://127.0.0.1:8723/m
```

That opens only the `/m` path, which is where ClipForge's video addresses live. `tailscale funnel status` shows what is open, including anything you already serve. Then in ClipForge, **Settings**, **Posting**, set **Public address** to your Mac's Funnel address, like `https://your-mac.your-tailnet.ts.net`, with no path.

Before the first post in this mode, ClipForge fetches the address itself the way Instagram would. If that fails, the post says why ("Instagram can't reach this Mac. Check that Tailscale Funnel is on for ...") and waits for Retry. Whether Meta's fetcher accepts a Funnel address is only known after a real post; if it doesn't, use Instagram through Facebook for that account.

ClipForge never changes Tailscale's settings itself. To close the path again, see `tailscale funnel --help` for the `off` form of the same command.

## Both ways

- **Copyright matches.** Instagram checks each video before it goes up. A post it finds a match in is never published on its own: it moves to **Needs you** in the Queue with Instagram's finding, and you choose to publish anyway or cancel.
- **The daily cap.** ClipForge reads each account's publishing quota before posting. **Instagram posts a day** in Settings, Posting sets your own cap, and Settings warns when it is high.
- **The video.** ClipForge checks every render against Meta's rules before uploading (MP4, H.264, AAC at 48 kHz, 3 seconds to 15 minutes, at most 300 MB), so a post isn't turned down after the wait.
- **When Meta says no.** Spam or restriction warnings pause that account and are never retried on their own; open the Instagram app to see what it says. A full cap moves the post past Instagram's 24 hours.

## Trial reels

A trial reel is shown at first only to people who don't follow the account, so you can see how a clip does with new viewers before your followers see it.

In the Post dialog's Details step, turn on **Trial reel** for an Instagram post (or in the **Every Instagram post** block for all of them). While it's on, it takes the place of **Share to feed**, and a second switch decides what happens next:

- **Share with followers if it does well** on: Instagram shares the reel with the account's followers by itself when it does well.
- Off: it stays a trial until you share it from the Instagram app.

Trial reels go up only through Meta's API, so either way above works, and an account linked with **Instagram in Chrome** can't post one: the Post dialog marks that post in red until you turn the switch off.

## The first post

Post one Reel, then look at it from a logged out browser or another account. Meta doesn't say whether posts from a Development mode app are seen by everyone, so check before posting more.
