# A clip's look

ClipForge picks a look for every clip (its layout, captions, hook title and gameplay), and every part of it is yours to change. This guide covers the parts added last: style kits, the cold open, your own voiceover and the starter gameplay. A clip's other settings are in its editor, on the **Look** tab, and the look new clips start with is in **Settings**, **Default look**.

## Style kits: a look per account

A style kit is one account's own look. Posts to that account wear it, so a gaming account and a podcast account can post the same clip looking like two different channels.

On **Accounts**, press **Settings** on the account's card and find **Style kit**. Each field starts at **The clip's own**, which leaves that part as the clip has it:

- **Caption style** and **Font**: the captions' look and their font.
- **Accent**, **Hook box colour** and **Hook text colour**: the highlight colour and the hook title's box and words.
- **Credit format**: the credit line, with `{channel}` where the channel's name goes, like `Clip: {channel}`.
- **Gameplay**: whether that account's posts show gameplay under the speaker.

Press **Save**. A kit's fields win over the clip's own edits for that account's posts; accounts without a kit post the clip as it is.

When a clip looks different from an account's kit, ClipForge renders a version of the clip for that account before it posts. The Post dialog says so on that post: "Renders this account's version first." The post waits while it renders (the Queue shows it as rendering) and then goes up. That version is never the clip's own: the editor still shows and renders the clip as you made it.

The same happens with gameplay. When one account has already posted a clip with a stretch of gameplay, another account's post of that clip gets a stretch of its own, so no two accounts show the same frames. If a version fails to render, its post fails saying why, and **Retry** on the post renders it again.

## The cold open

A cold open starts the clip on its strongest line, two to four seconds from inside the clip, then a whoosh into the start. It's a strong hook when the clip's best moment comes late.

In the editor, on the **Look** tab, turn on **Cold open**. Its hint shows the line it would open on. When ClipForge found no line worth opening on, the switch is off and says so. The clip renders again with it.

## Your own voiceover

A voiceover puts your own words over the clip: the clip's speech drops under your voice while you talk, and comes back up when you stop. It also counts as one of the clip's original edits.

In the editor, on the **Sound** tab, find **Voiceover**:

1. Press **Record** and speak. The first time, Chrome asks whether ClipForge's window may use the microphone (and macOS may ask about Chrome); allow it.
2. Press **Stop**, then listen to the take.
3. Press **Use this take** to keep it, or **Record again**.

The clip renders again with your voice. **Remove** takes the voiceover off and renders the clip without it. To use a recording you made elsewhere, press **Choose a file** instead of **Record**.

## Starter gameplay

Split-screen clips show gameplay under the speaker. ClipForge comes with a starter list of gameplay videos whose uploaders say they're free to use, so the split screen works before you add your own.

Open **Library** and find **Starter list**. Each entry shows the game, its uploader, its length, its licence and the uploader's own terms, with its limits marked. Nothing downloads until you press **Download** on an entry; a download that failed offers **Download again**. Once it's in your library, clips can use it, and **New random segment** on a clip's **Look** tab renders it with another stretch of the same video.

Read each entry's terms before you use it:

- Minecraft entries are for personal channels only. Mojang's usage guidelines don't allow them in brand or agency work, so each says "Personal channels only, not for brand or agency work (Mojang's usage guidelines)". Keep them off client or paid brand posts.
- Some uploaders say "free to use" in their description without a written licence. Those entries say "Informal permission only: keep it off paid or brand work".
- Every entry comes with a credit line, and clips that use it carry it.

For your own gameplay, press **Add gameplay** on the Library page with who made it and the licence that lets you use it.
