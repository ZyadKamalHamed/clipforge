# Your music library

ClipForge can lay music under a clip, but only from your own library: tracks you own or have a licence for. It never bundles music and never mixes a platform's sounds into a file (for those, see [trends.md](trends.md)). Music is off for a clip until you turn it on in the editor.

## Adding tracks

Open **Library**, then the **Music** tab, press **Add music** and choose the file. MP3, M4A, AAC, WAV, AIFF and FLAC files work, and so does an MP4, M4V or MOV with sound (only its sound is used). A file with no sound ClipForge can read, or a track under a second long, is refused with a sentence saying so. ClipForge keeps its own copy in its library folder, and your file stays where it was.

Each track asks for:

- **Title** (left blank, it is the file's name) and **Artist**, who made the track, as a caption credit names them.
- **Where it's from**, an optional link to the track's page.
- **Licence**, which is required: what the licence lets you do, such as "Bought from Artlist for social use". **Licence link** is optional.
- **The licence asks for credit**, ticked when it does.
- **Moods** and **Energy**, below.

Each row plays the track (one at a time), shows its length and how many clips use it, and has **Edit** and **Delete**. Delete always asks first, and when clips use the track it says how many: they have no music the next time they render.

## Licence and credit

The licence note is for you: ClipForge can't check a licence, so it keeps your word for what the track allows next to the track. When **The licence asks for credit** is ticked, every post of a clip rendered with that track gets a credit line in its caption, such as "Music: Night Drive by Lena Hart". It goes before a campaign's required lines, which still end the post, and an edited caption gets it too unless it already has it. When a clip is rendered again after its post was approved, the post's credit follows the music in the new render: a changed track swaps the name, and music turned off takes the line out. Export's caption.txt does the same for the video it copies. The editor says so under the track: "Its licence asks for credit, so the caption names it."

## Moods and energy

The packaging pass gives each clip a mood (hype, funny, inspiring, serious, emotional, calm, dark or curious) and an energy from 1 (calm) to 5 (all out). Tag each track with the moods it fits and its energy, and ClipForge can match tracks to clips. A track suits a clip when one of its moods is the clip's or close to it (calm sits next to emotional and curious, for instance), or, failing that, when its energy is within 1 of the clip's. The clip's own mood scores highest, then a close mood, then how near the energies are. A track with no moods and no energy is never suggested, though you can still pick it by name.

## Picking music for a clip

In the clip editor's **Sound** tab, **Music** offers Off, **Best match from your library**, then your tracks: the ones that suit the clip first, each saying why ("Night Drive, matches its calm mood"), then the rest. Pick one and re-render to hear it.

**Best match** picks the best-suiting track each time the clip renders, from the tracks whose files are still there; ties go by title. When no track suits the clip, it renders with no music and the version says so: "No track in your music library matches this clip's mood, so it has none". A track you picked by name that has since been deleted gives a render with no music too, saying the track is gone, rather than a different track.

## The level and ducking

**Music level** (shown once music is on) sets how far under the speech the music sits, from 24 dB under to 6 dB under; 12 dB under is the default. The music is levelled against the clip's own speech, so the same setting sounds the same on a quiet clip and a loud one. A clip with no speech gets the music at a steady background level instead, and the final levelling below then brings the whole mix up to normal loudness, so the music ends up louder than that level. Any sound in the source louder than near silence (-70 LUFS) counts as speech here, game audio included, so on a gameplay clip the music sits under the game and ducks on its loud stretches.

While someone speaks, the music ducks a further 6 dB: it drops within 30 ms of the first word and comes back over 300 ms after the last, so it fills the gaps without fighting the voice. A track shorter than the clip loops, each loop crossfading into the next over 50 ms. The music starts with a 5 ms fade-in, so there is no click at the first frame, and fades out over the clip's last 300 ms. The whole mix is then brought to -14 LUFS with everything else, as every clip is.
