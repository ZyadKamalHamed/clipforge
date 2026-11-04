# Posting to TikTok

ClipForge posts to TikTok by driving your own Chrome, in a window you can see, through TikTok Studio's upload page. Each TikTok account gets a Chrome profile of its own, so its login stays separate from your everyday Chrome and from every other account. TikTok's official posting API is not used. Nothing posts until you press Post in ClipForge, or approve a clipping channel's drafts, and then press Confirm.

## Link an account

1. Open **Accounts**, press **Add account** and pick **TikTok in Chrome**.
2. Press **Log in with Chrome**. A Chrome window opens on TikTok's own login page.
3. Log in there with TikTok's Use QR code. On your phone, switch the TikTok app to the account you are linking, since that is the one that gets linked. Then scan the code with the scanner inside the TikTok app (TikTok's page says where it is, beside the code) and tap Confirm on your phone. Your phone's camera app only opens TikTok and doesn't log you in. The code runs out after a minute or two, so reload the page if it says it has expired. If you made the account with Google, the QR code still works. Other ways in don't work in this window: Continue with Google fails because Google won't sign in to a browser a program drives, and ClipForge doesn't hide that it drives this one, and TikTok's password login can say there have been too many attempts. ClipForge never sees your password and never types into the login form.
4. When TikTok Studio opens, ClipForge reads the account's handle and closes the window. The card appears on Accounts.

The login stays on this Mac, in `~/ClipForge/browser/<account id>`, readable only by you. Repeat for each account; six is the design case, and more is fine.

**Two step verification.** If TikTok asks for a code by text, email or an authenticator app, type it into TikTok's page in that same window. ClipForge just waits. It gives up after 10 minutes with "No TikTok login came through in 10 minutes. Press Log in to try again.", and closing the window early stops the login without harm.

**Logging in again.** When TikTok ends a session, any page lands on its login screen and the card shows **Log in again**. Press it and log in the same way; the account keeps its settings and history.

## Before the first post

Press **Check** on the card. The self-test opens the upload page, confirms the session is live and that every part of the page ClipForge posts with is there, uploads nothing and closes. If something is missing, TikTok has changed its page, and `clipforge/post/browser/selectors/tiktok.json` needs the new selector before that account can post.

Then make the first post an assisted one: turn on **Assisted posting** in the account's settings. ClipForge fills in everything and stops before the last click, with a banner at the top of the page: "ClipForge has filled this in. Check it and press Post." Look over the caption, hashtags, visibility and switches, then press Post yourself. ClipForge still sees the post go up and records its link. Turn Assisted posting off once a post or two have come out right.

A post with a TikTok sound picked for it always runs assisted: the banner names the sound, you add it with TikTok Studio's own editor, then press Post.

## What happens during a post

At the post's time ClipForge opens the account's Chrome window on the upload page, chooses the video, waits for TikTok to process it, writes the caption, types each hashtag and picks the exact match TikTok suggests, sets visibility and the comment, duet and stitch switches, waits for TikTok's copyright check when there is one, and presses Post. When TikTok shows the post on its content page, ClipForge reads the link and closes the window.

**The pacing.** Between steps ClipForge waits 0.4 to 1.5 seconds, and it types hashtags a character at a time, because TikTok's editor needs time to react. That is for reliability, not disguise: ClipForge uses your installed Chrome in a normal window, with no stealth add-ons, altered fingerprints, proxies or CAPTCHA solving, and it never changes a video to slip past duplicate checks. Only one ClipForge Chrome window runs at a time, so posts and logins for several accounts go one after another; a login waiting its turn says **Waiting for Chrome** on its card.

Keep your hands off that window while it works. Typing or clicking in it can land in the caption.

## Needs you

A post moves to **Needs you** when TikTok shows something ClipForge doesn't expect: a new pop up, a missing button, "Something went wrong", or a check it can't read. ClipForge saves a screenshot and the page to the post's log, leaves the window open with a banner, and waits for you.

You can finish the post by hand in that window; ClipForge still notices it go up and records the link. Or open the **Queue**, where the post sits in the Needs you section at the top, and read its log, then Retry, Reschedule, Export the video and caption to post yourself (then press Mark as posted on it, with its link), or Cancel post.

A post never holds up the others for long. After the **Minutes a post waits for you** setting (Settings, Posting, 10 minutes by default) ClipForge gives the window and the posting lane back, the rest carry on, and the post takes the next free slot once you act. If the Mac was asleep when a post came due, it goes out on wake while its posting window is still open.

## Limits that stay on

The caps and spacing in Settings, Posting hold for every account: TikTok posts a day, the hours between posts and the gap between platforms. **Pause posting** at the top of the window stops every post before its last click. Pausing an account, or removing it, deletes nothing on TikTok.
