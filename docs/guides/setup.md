# Setting up ClipForge

This guide goes from a fresh Mac to your first real post, in order. Each step says what ClipForge does and what is left to you. Nothing ever posts until you press **Confirm and post** (or approve a clipping channel's drafts and then confirm).

## 1. Install

You need macOS on Apple silicon with Homebrew, ffmpeg (`brew install ffmpeg`), [uv](https://docs.astral.sh/uv/) (`brew install uv`), Node.js 22 or later (`brew install node`), Google Chrome and, for the AI parts, Claude Code logged in to your Claude subscription. For the notifier helper inside the app you also need the Xcode command line tools (`xcode-select --install`), which bring `swiftc`.

From the ClipForge folder, run:

```sh
scripts/install.sh
```

It checks for everything above and says how to get anything missing, installs the Python and web parts, downloads the speech and face models, runs `clipforge doctor` and builds ClipForge.app in `~/Applications`. To see what's missing without changing anything, run `scripts/install.sh --check`.

## 2. Check the doctor

`uv run clipforge doctor` checks Python, ffmpeg, yt-dlp and its JavaScript runtime, Claude Code, the speech and face models, the video encoder, the fonts, Chrome, the Keychain, the background service and the free disk, and says how to fix anything that fails. The same checks are in the window, under **Settings**, **Doctor**. Run it again whenever something stops working; it changes nothing on its own.

## 3. Open the app

Open ClipForge.app from `~/Applications`, or run `uv run clipforge serve --open` from the ClipForge folder. ClipForge opens in its own Chrome window. Your projects, renders and settings live in `~/ClipForge`; set `CLIPFORGE_HOME` before starting to use another folder.

## 4. The background service

The daily check of your clipping channels, the morning notification and scheduled posts need ClipForge running. Opened from ClipForge.app, it keeps running after you close its window, until you log out or restart the Mac. With the background service, macOS starts it each time you log in, so they go on after a restart without you opening it.

Turn it on in **Settings**, **Posting**, **Background service**: press **Install**. **Remove** turns it off again, and asks first. From Terminal, `uv run clipforge service install`, `uv run clipforge service status` and `uv run clipforge service uninstall` do the same.

## 5. Your first project

Drop a YouTube link (or a video file) anywhere in the window, or paste one. ClipForge downloads it, transcribes it on this Mac, finds the moments worth clipping, renders them and shows them in the project's gallery. Open a clip to watch it, change its words, look and sound, and render it again. [The look guide](look.md) covers the parts of a clip's look that are yours to set: style kits, the cold open, your own voiceover and the starter gameplay.

## 6. Link your accounts

Each platform has its own guide:

- [Posting to TikTok](tiktok.md): logging in each account once in its own Chrome window, the self-test, assisted posts and what Needs you means.
- [Posting to Instagram](instagram.md): Creator or Business accounts, linking through a Facebook Page or with Instagram login, trial reels and the public address the second way needs.

Both start on **Accounts**, with **Add account**. ClipForge never sees a password: every login happens on the platform's own page.

## 7. The first real posts

Nothing in the build logged in, uploaded or posted anywhere, so the first real posts are yours, in this order. You don't need to keep this list open: **the path** on Today walks you through every one of these, a step at a time on your To do list, and ticks most of them itself (see [Today](today.md)).
1. Rebuild the app with `scripts/make_app.sh`, so it has the notifier helper, and allow ClipForge's first notification when macOS asks.
2. Log in each TikTok account, then press Check on its card to run the self-test.
3. Make the first TikTok post an assisted one: ClipForge fills everything in and you press TikTok's own Post (or Schedule) yourself.
4. Link Instagram through Facebook, post one Reel and look at it logged out, to see that everyone can.
5. Note the live Instagram cap ClipForge reads before each post.
6. Use Instagram with Instagram login only if the Facebook way won't do.
7. Do one real morning on **Today**, from the notification to done.
8. Post one Reel with a trending sound, to see whether Meta takes it.
9. Post one trial reel on an Instagram account linked through Facebook, to see how Meta shows it.
10. Install the background service, log out and back in without opening ClipForge, and check that the morning notification still comes.

After that, select clips in a project and press **Post**: the Post dialog picks the accounts, plans each post's time and caption, and sends nothing until **Confirm and post**. The **Queue** shows every post from then on.
