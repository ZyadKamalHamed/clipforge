# Trends and sounds

The **Trends** page (in the rail, between Library and Settings) holds the sounds you can pick for a post, the weekly lists of what is trending, and your hashtag sets. Most trending-sound data now lives inside the apps, so ClipForge reads what the platforms let it read and leaves the rest to you: Instagram's list arrives on its own, TikTok's is a shortlist you keep, and four weekly lists open in your browser.

## Where each list comes from

**Trending on Instagram.** For each Instagram account linked through Facebook, ClipForge asks Meta's official API once a day for two lists: trending music and trending original sounds. They come from Meta's cleared catalogue (original sounds and Sound Collection), not label hits. Each account has its own block with the time of its last read, the sounds by rank (title, artist, music or original sound, length) and an **Open** button that opens the sound's Instagram page in your default browser. **Check now** reads that account again; it is greyed out until the account's login is in the Keychain, and the block says to link it again on the Accounts page. When a read fails, the block says why in red with the time it last tried, and the list from the last good read stays. An account linked through Instagram Login gets no list, because Instagram doesn't offer trending sounds there; the line under the blocks names those accounts.

**TikTok shortlist.** TikTok shares no list of trending sounds that ClipForge may read: its Creative Center no longer lists songs, its trending-music API is for registered companies only, and the open-source scrapers for it are broken. So the shortlist is yours. In TikTok, open a sound's page, copy its link and paste it into **Sound link** (it looks like `https://www.tiktok.com/music/Late-Train-7234567890123456789`). A short `vm.tiktok.com` link is refused: open it in your browser first and paste the full link it lands on. **Title** is optional; left blank, it is the name in the link. Tag the sound with its region (AU or US), its moods and its energy from 1 (calm) to 5 (all out), so the suggestions can match it to a clip. Each row has **Edit**, **Remove** (posts that picked the sound keep it) and **Open**, which opens the sound's TikTok page in your default browser.

**Weekly lists.** Later, Buffer and SocialPilot each post a list of the sounds trending on TikTok and Instagram. The four buttons open them in your default browser; ClipForge never reads them.

## The New list out badge

Once a week ClipForge reads SocialPilot's public RSS feed (`https://www.socialpilot.co/feed`) for one thing: the date of its newest post. It is one GET request for the feed and one for the site's robots.txt, with no cookies and under a user agent that names ClipForge and its version, and the feed is read only when robots.txt allows it. When that date is newer than the last time you opened one of the weekly lists, **New list out** shows beside the heading. Until you have opened one of them for the first time it shows too, even with the weekly read switched off. Opening any of the four lists clears it until the next new post. SocialPilot's terms forbid data mining, so the feed is never read for anything else, and a refused or failed read is not tried again until the next week. The switch "Check SocialPilot's feed once a week for a new list" turns the weekly read off (and back on); it saves at once.

## Stale sounds and the filters

A sound added more than 14 days ago is marked **Stale**: trends move fast, so it may have peaked. A shortlist row shows the chip beside the date it was added. On an Instagram block the chip sits beside the read time, which happens only when the account's reads have failed for two weeks.

The filters at the top work on the two sound sections only. **Platform** shows one of them or both, **Mood** and **Region** keep the shortlist sounds tagged that way, and **Hide stale** leaves out the stale ones. Instagram doesn't say a sound's mood or region, so a Mood or Region filter hides its lists, and the page says so. The weekly lists and your hashtag sets always show.

## Hashtag sets

A hashtag set is the hashtags you use for a niche, named and ready to copy into a caption. Pick the niche (from the niche list in Settings), give the set a name (up to 60 characters) and type the hashtags, separated by spaces or commas, with or without the #. A set holds at most 30 hashtags; a repeat is dropped, whatever its case, keeping the first spelling. **Copy** puts the whole set on your clipboard as `#gaming #clips #fyp`. **Edit** changes it in place, and **Delete** asks first; captions that already have its hashtags keep them. The packaging pass still writes each clip's own hashtags from its topic, so a set is for the ones you always add.

## Picking a sound for a post

A sound is picked per post, in the **Post** dialog. An account that can take one gets a **Sound** field listing None, then up to five sounds that suit the clip (under "Suits this clip"), then the rest of the list. For TikTok those are the shortlisted sounds that match the clip's mood and energy; for Instagram they are the account's trending sounds in Instagram's own order, since Instagram doesn't say a sound's mood. Your music tracks are counted apart from the sounds, so a good track match never pushes a sound out of that group.

- **Instagram, linked through Facebook:** the field lists that account's trending sounds. Instagram adds the sound to the Reel on its side, with the video at full volume and the sound at the **Sound level** you set (15 out of 100 unless you move it). Nothing is mixed into the file.
- **TikTok:** the field lists your shortlist. A picked sound makes the post assisted, marked "Assisted: you add the sound": ClipForge fills in the post in that account's Chrome window and stops with a banner naming the sound, and you add it with TikTok Studio's own editor and press Post or Schedule yourself (see [tiktok.md](tiktok.md)). ClipForge never presses Post or Schedule for an assisted post.
- **Instagram through Instagram Login, or through Chrome:** there is no Sound field, since Instagram offers those routes no trending sounds.

The clip editor's Sound tab shows the shortlisted sounds that suit the clip, by mood and energy, under "Trending sounds for this clip", each with Open, so you can listen before you post.

Instagram can turn a post down over its sound, for instance when the sound has left its catalogue since you picked it. The Queue then offers **Post without the sound**, which asks first and schedules the post again at the next free slot with the clip's own audio only. A scheduled Instagram post with a sound has the same button, and keeps its time.

## What ClipForge never does

ClipForge never downloads a platform's sounds, never mixes TikTok's Commercial Music Library, Instagram's Sound Collection or a label's tracks into a file, and never calls unofficial TikTok endpoints. A pasted TikTok link is read as text and never fetched. Instagram's download links, cover art and profile pictures for a sound are never stored, fetched or shown. No list site is scraped: not Later, Buffer, SocialPilot or tokchart pages; SocialPilot's feed date is the only thing read. Music that does go into the file comes only from your own library, with its licence noted (see [music.md](music.md)).
